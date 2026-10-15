import tempfile
import unittest
from pathlib import Path
from shift_ledger_pos import create_app, create_user


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=str(Path(self.temp.name)/'web.sqlite3')
        self.app=create_app({'TESTING':True,'DATABASE':self.path,'SECRET_KEY':'test-only-secret'})
        create_user(self.path,'admin','test-password-long','admin')
        create_user(self.path,'cashier','test-password-long','cashier')
        self.client=self.app.test_client();self.csrf=self.client.get('/api/session').json['csrf']
    def post(self,path,body):return self.client.post(path,json=body,headers={'X-CSRF-Token':self.csrf})
    def login(self,name='admin'):
        response=self.post('/api/login',{'username':name,'password':'test-password-long'})
        self.assertEqual(response.status_code,200);self.csrf=response.json['csrf']
    def test_anonymous_state_denied(self):self.assertEqual(self.client.get('/api/state').status_code,401)
    def test_csrf_missing_denied(self):self.assertEqual(self.client.post('/api/login',json={'username':'admin','password':'test-password-long'}).status_code,403)
    def test_login_rotates_csrf(self):
        old=self.csrf;self.login();self.assertNotEqual(old,self.csrf)
    def test_cashier_cannot_change_stock(self):
        self.login('cashier');self.assertEqual(self.post('/api/products',{'sku':'a'}).status_code,403)
    def test_cashier_cannot_refund(self):
        self.login('cashier');self.assertEqual(self.post('/api/sales/1/refunds',{}).status_code,403)
    def test_logout_invalidates_auth(self):
        self.login();self.post('/api/logout',{});self.assertEqual(self.client.get('/api/state').status_code,401)
    def test_wrong_content_type(self):
        self.assertEqual(self.client.post('/api/login',data='{}',headers={'X-CSRF-Token':self.csrf}).status_code,415)
    def test_wrong_payload_shape(self):
        self.assertEqual(self.post('/api/login',[]).status_code,400)
    def test_login_throttled(self):
        for _ in range(5):self.assertEqual(self.post('/api/login',{'username':'admin','password':'wrong'}).status_code,401)
        self.assertEqual(self.post('/api/login',{'username':'admin','password':'wrong'}).status_code,429)
    def test_headers(self):
        response=self.client.get('/api/session');self.assertEqual(response.headers['Cache-Control'],'no-store');self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
    def test_full_http_sale_and_receipt(self):
        self.login();pid=self.post('/api/products',{'sku':'A','name':'<script>alert(1)</script>','price_cents':200,'stock':1}).json['id']
        shift=self.post('/api/shifts',{'opening_cents':0}).json['id']
        response=self.post('/api/sales',{'idempotency_key':'http-request-0001','shift_id':shift,'payment':'cash','items':[{'product_id':pid,'quantity':1}],'tendered_cents':500})
        self.assertEqual(response.status_code,201);self.assertEqual(response.json['change_cents'],300)
        receipt=self.client.get('/receipts/'+str(response.json['id']));self.assertIn(b'&lt;script&gt;',receipt.data);self.assertNotIn(b'<script>alert',receipt.data)
        export=self.client.get('/api/export/sales.csv');self.assertEqual(export.status_code,200);self.assertIn(b'total_minor',export.data)
