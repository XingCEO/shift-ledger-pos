import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from shift_ledger_pos.db import initialize, transaction, connect
from shift_ledger_pos import service


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'test.sqlite3')
        initialize(self.path)
        with transaction(self.path) as db:
            db.execute("INSERT INTO users(id,username,password_hash,role) VALUES(1,'admin','unused','admin')")
        self.product = service.create_product(self.path, dict(sku='A',name='Coffee',category='Coffee',price_cents=101,tax_bps=500,stock=10))['id']
        self.shift = service.open_shift(self.path, 1, {'opening_cents':10000})['id']

    def sale_payload(self, **changes):
        return {**dict(idempotency_key='sale-request-001',shift_id=self.shift,items=[{'product_id':self.product,'quantity':1}],payment='cash',tendered_cents=1000),**changes}

    def refund_payload(self, receipt, key='refund-request-001', quantity=1):
        return dict(idempotency_key=key,shift_id=self.shift,items=[{'sale_item_id':receipt['items'][0]['id'],'quantity':quantity}],reason='Customer return')

    def test_sale_updates_stock_and_receipt(self):
        sale=service.sell(self.path,1,self.sale_payload())
        self.assertEqual((sale['subtotal_cents'],sale['tax_cents'],sale['total_cents'],sale['change_cents']),(101,5,106,894))
        self.assertEqual(service.state(self.path)['products'][0]['stock'],9)

    def test_duplicate_request_is_same_receipt(self):
        a=service.sell(self.path,1,self.sale_payload());b=service.sell(self.path,1,self.sale_payload())
        self.assertEqual(a['id'],b['id']);self.assertEqual(service.state(self.path)['products'][0]['stock'],9)

    def test_key_reuse_with_other_body_is_rejected(self):
        service.sell(self.path,1,self.sale_payload())
        with self.assertRaises(service.DomainError):service.sell(self.path,1,self.sale_payload(tendered_cents=2000))

    def test_insufficient_tender_leaves_no_sale(self):
        with self.assertRaises(service.DomainError):service.sell(self.path,1,self.sale_payload(tendered_cents=0))
        state=service.state(self.path);self.assertEqual(state['sales'],[]);self.assertEqual(state['products'][0]['stock'],10)

    def test_last_item_cannot_be_sold_twice_concurrently(self):
        service.adjust_stock(self.path,self.product,{'delta':-9,'reason':'Fixture'})
        def attempt(key):
            try:return service.sell(self.path,1,self.sale_payload(idempotency_key=key))['id']
            except service.DomainError:return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(attempt,['concurrent-sale-1','concurrent-sale-2']))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(service.state(self.path)['products'][0]['stock'],0)

    def test_concurrent_retries_do_not_duplicate_sale(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:service.sell(self.path,1,self.sale_payload())['id'],range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(len(service.state(self.path)['sales']),1)

    def test_partial_refunds_sum_to_original_total(self):
        with transaction(self.path) as db:db.execute('UPDATE products SET tax_bps=333 WHERE id=?',(self.product,))
        sale=service.sell(self.path,1,self.sale_payload(items=[{'product_id':self.product,'quantity':3}]))
        amounts=[service.refund(self.path,1,sale['id'],self.refund_payload(sale,f'refund-rounding-{i}'))['total_cents'] for i in range(3)]
        self.assertEqual(amounts,[104,104,105]);self.assertEqual(sum(amounts),sale['total_cents']);self.assertEqual(service.state(self.path)['products'][0]['stock'],10)

    def test_refund_cannot_exceed_original_quantity(self):
        sale=service.sell(self.path,1,self.sale_payload());service.refund(self.path,1,sale['id'],self.refund_payload(sale))
        with self.assertRaises(service.DomainError):service.refund(self.path,1,sale['id'],self.refund_payload(sale,'second-refund-attempt'))

    def test_refund_retry_is_idempotent(self):
        sale=service.sell(self.path,1,self.sale_payload());payload=self.refund_payload(sale)
        a=service.refund(self.path,1,sale['id'],payload);b=service.refund(self.path,1,sale['id'],payload)
        self.assertEqual(a,b);self.assertEqual(service.state(self.path)['products'][0]['stock'],10)

    def test_cash_reconciliation_excludes_card(self):
        service.sell(self.path,1,self.sale_payload())
        service.sell(self.path,1,self.sale_payload(idempotency_key='card-payment-001',payment='card'))
        result=service.close_shift(self.path,{'shift_id':self.shift,'counted_cents':10100})
        self.assertEqual(result['expected_cents'],10106);self.assertEqual(result['difference_cents'],-6)

    def test_prior_shift_refund_uses_current_drawer(self):
        sale=service.sell(self.path,1,self.sale_payload())
        service.close_shift(self.path,{'shift_id':self.shift,'counted_cents':10106})
        self.shift=service.open_shift(self.path,1,{'opening_cents':1000})['id']
        service.refund(self.path,1,sale['id'],self.refund_payload(sale))
        self.assertEqual(service.state(self.path)['shift']['expected_cents'],894)

    def test_new_sale_after_close_rejected_but_retry_allowed(self):
        sale=service.sell(self.path,1,self.sale_payload())
        service.close_shift(self.path,{'shift_id':self.shift,'counted_cents':10106})
        self.assertEqual(service.sell(self.path,1,self.sale_payload())['id'],sale['id'])
        with self.assertRaises(service.DomainError):service.sell(self.path,1,self.sale_payload(idempotency_key='closed-shift-new'))

    def test_refund_uses_original_price(self):
        sale=service.sell(self.path,1,self.sale_payload())
        with transaction(self.path) as db:db.execute('UPDATE products SET price_cents=5000 WHERE id=?',(self.product,))
        result=service.refund(self.path,1,sale['id'],self.refund_payload(sale))
        self.assertEqual(result['total_cents'],106)

    def test_multi_line_failure_is_atomic(self):
        with self.assertRaises(service.DomainError):service.sell(self.path,1,self.sale_payload(items=[{'product_id':self.product,'quantity':1},{'product_id':999,'quantity':1}]))
        self.assertEqual(service.state(self.path)['products'][0]['stock'],10)

    def test_negative_adjustment_cannot_make_negative_stock(self):
        with self.assertRaises(service.DomainError):service.adjust_stock(self.path,self.product,{'delta':-11,'reason':'Waste'})

    def test_bool_quantity_is_not_integer(self):
        with self.assertRaises(service.DomainError):service.sell(self.path,1,self.sale_payload(items=[{'product_id':self.product,'quantity':True}]))

    def test_double_open_rejected(self):
        with self.assertRaises(service.DomainError):service.open_shift(self.path,1,{'opening_cents':0})

    def test_stock_ledger_matches_current_stock(self):
        sale=service.sell(self.path,1,self.sale_payload());service.refund(self.path,1,sale['id'],self.refund_payload(sale))
        service.adjust_stock(self.path,self.product,{'delta':3,'reason':'Delivery'})
        db=connect(self.path)
        try:self.assertEqual(db.execute('SELECT sum(delta) FROM stock_movements WHERE product_id=?',(self.product,)).fetchone()[0],13)
        finally:db.close()

    def test_catalog_edit_keeps_receipt_snapshot(self):
        sale=service.sell(self.path,1,self.sale_payload())
        service.update_product(self.path,self.product,dict(name='New name',category='Retail',price_cents=900,tax_bps=1000,active=0))
        current=service.state(self.path)
        self.assertEqual(current['sales'][0]['items'][0]['name'],'Coffee')
        self.assertEqual(current['products'][0]['active'],0)
        with self.assertRaises(service.DomainError):service.sell(self.path,1,self.sale_payload(idempotency_key='archived-sale-001'))
