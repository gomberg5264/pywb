from pywb.webapp.pywb_init import create_wb_router
from pywb.framework.wsgi_wrappers import init_app

from wsgiref.simple_server import make_server

from pywb.framework.proxy_resolvers import CookieResolver

import threading
import requests
import shutil
import os

TEST_CONFIG = 'tests/test_config_proxy.yaml'

TEST_CA_DIR = './tests/pywb_test_certs'
TEST_CA_ROOT = './tests/pywb_test_ca.pem'

server = None
sesh_key = None

def setup_module():
    global server
    server = ServeThread()
    server.daemon = True
    server.start()
     
    global session   
    session = requests.Session()
 

def teardown_module():
    try:
        server.httpd.shutdown()
        threading.current_thread().join(server)
    except Exception:
        pass

    # delete test root and certs
    shutil.rmtree(TEST_CA_DIR)
    os.remove(TEST_CA_ROOT)


class ServeThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        super(ServeThread, self).__init__(*args, **kwargs)
        self.app = init_app(create_wb_router,
                            load_yaml=True,
                            config_file=TEST_CONFIG)
 
        # init with port 0 to allow os to pick a port
        self.httpd = make_server('', 0, self.app)
        port = self.httpd.socket.getsockname()[1]

        proxy_str = 'http://localhost:' + str(port)
        self.proxy_dict = {'http': proxy_str, 'https': proxy_str}

    def run(self, *args, **kwargs):
        self.httpd.serve_forever()


class TestHttpsProxy:
    def setup(self):
        self.session = requests.Session()

    def get_url(self, url, headers=None):
        global sesh_key
        if sesh_key:
            self.session.headers.update({'Cookie': '__pywb_proxy_sesh=' + sesh_key})
            self.session.cookies.set('__pywb_proxy_sesh', sesh_key, domain='.pywb.proxy')
            #self.session.cookies.set('__pywb_proxy_sesh', sesh_key, domain='.iana.org')

        return self.session.get(url,
                           proxies=server.proxy_dict,
                           verify=TEST_CA_ROOT)
 
    def test_replay_no_coll(self):
        resp = self.get_url('https://iana.org/')
        assert resp.url == 'https://select.pywb.proxy/https://iana.org/'
        assert resp.status_code == 200

    def test_replay_set_older_coll(self):
        resp = self.get_url('https://older-set.pywb.proxy/https://iana.org/')
        assert resp.url == 'https://iana.org/'
        assert resp.status_code == 200
        assert '20140126200624' in resp.text
        
        sesh1 = self.session.cookies.get('__pywb_proxy_sesh', domain='.pywb.proxy')
        sesh2 = self.session.cookies.get('__pywb_proxy_sesh', domain='.iana.org')
        assert sesh1 and sesh1 == sesh2, self.session.cookies
        
        # store session cookie
        global sesh_key
        sesh_key = sesh1

        global sesh_key
        sesh2 = self.session.cookies.get('__pywb_proxy_sesh', domain='.iana.org')
        assert sesh_key == sesh2

    def test_replay_same_coll(self):      
        resp = self.get_url('https://iana.org/')
        assert resp.url == 'https://iana.org/'
        assert resp.status_code == 200
        assert 'wbinfo.proxy_magic = "pywb.proxy";' in resp.text
        assert '20140126200624' in resp.text

    def test_replay_set_change_coll(self):
        resp = self.get_url('https://all-set.pywb.proxy/https://iana.org/')
        assert resp.url == 'https://iana.org/'
        assert resp.status_code == 200
        assert '20140127171238' in resp.text
        
        # verify still same session cookie
        sesh2 = self.session.cookies.get('__pywb_proxy_sesh', domain='.iana.org')
        global sesh_key
        assert sesh_key == sesh2

    def test_query(self):
        resp = self.get_url('https://query.pywb.proxy/*/https://iana.org/')
        assert resp.url == 'https://query.pywb.proxy/*/https://iana.org/'
        assert resp.status_code == 200
        assert 'text/html' in resp.headers['content-type']
        assert '20140126200624' in resp.text
        assert '20140127171238' in resp.text
        assert '<b>3</b> captures' in resp.text

    # testing via http here
    def test_change_timestamp(self):
        resp = self.get_url('http://query.pywb.proxy/20140126200624/http://iana.org/')
        assert resp.url == 'http://iana.org/'
        assert resp.status_code == 200
        assert '20140126200624' in resp.text

    def test_change_coll_same_ts(self):
        resp = self.get_url('https://all-set.pywb.proxy/iana.org/')
        assert resp.url == 'https://iana.org/'
        assert resp.status_code == 200
        assert '20140126200624' in resp.text

    # testing via http here
    def test_change_latest_ts(self):
        resp = self.get_url('http://query.pywb.proxy/http://iana.org/?_=1234')
        assert resp.url == 'http://iana.org/?_=1234'
        assert resp.status_code == 200
        assert '20140127171238' in resp.text

    def test_diff_url(self):
        resp = self.get_url('https://example.com/')
        assert resp.url == 'https://example.com/'
        assert '20140127171251' in resp.text

    # Bounce back to select.pywb.proxy due to missing session
    def test_clear_key(self):
        # clear session key
        global sesh_key
        sesh_key = None

    def test_no_sesh_latest_bounce(self):
        resp = self.get_url('https://query.pywb.proxy/https://iana.org/')
        assert resp.url == 'https://select.pywb.proxy/https://iana.org/'

    def test_no_sesh_coll_change_bounce(self):
        resp = self.get_url('https://auto.pywb.proxy/https://iana.org/')
        assert resp.url == 'https://select.pywb.proxy/https://iana.org/'

    def test_no_sesh_ts_bounce(self):
        resp = self.get_url('https://query.pywb.proxy/20140126200624/https://iana.org/')
        assert resp.url == 'https://select.pywb.proxy/20140126200624/https://iana.org/'

    def test_no_sesh_query_bounce(self):
        resp = self.get_url('https://query.pywb.proxy/*/https://iana.org/')
        assert resp.url == 'https://select.pywb.proxy/https://query.pywb.proxy/*/https://iana.org/'

    # static replay
    def test_replay_static(self):
        resp = self.get_url('https://pywb.proxy/static/default/wb.js')
        assert resp.status_code == 200
        found = u'function init_banner' in resp.text
        assert found, resp.text

    # download index page and cert downloads
    def test_replay_dl_page(self):
        resp = self.get_url('https://pywb.proxy/')
        assert resp.status_code == 200
        assert 'text/html' in resp.headers['content-type']
        found = u'Download' in resp.text
        assert found, resp.text

    def test_dl_pem(self):
        resp = self.get_url('https://pywb.proxy/pywb-ca.pem')

        assert resp.headers['content-type'] == 'application/x-x509-ca-cert'

    def test_dl_p12(self):
        resp = self.get_url('https://pywb.proxy/pywb-ca.p12')

        assert resp.headers['content-type'] == 'application/x-pkcs12'

