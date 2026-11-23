"""Exercise mock navigation and browser Geolocation bridge without real location upload."""
import os
from playwright.sync_api import sync_playwright, expect


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('CHROME_PATH','/usr/bin/google-chrome'),headless=True,args=['--no-sandbox'])
        context=browser.new_context(permissions=['geolocation'],geolocation={'longitude':116.4,'latitude':39.9,'accuracy':5},viewport={'width':1440,'height':1000})
        page=context.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        base=os.environ.get('BROWSER_BASE_URL','http://127.0.0.1:8002')
        page.goto(base+'/navigation')
        page.locator('#map-mode').select_option('mock')
        expect(page.locator('#map-notice')).to_contain_text('模拟模式')
        expect(page.locator('#map-mode')).to_have_value('mock')
        expect(page.locator('#map-notice')).to_contain_text('虚构')
        page.locator('#mock-start').click()
        expect(page.locator('#location-info')).to_contain_text('mock')
        page.locator('#destination-query').fill('图书馆')
        page.locator('#search-button').click()
        expect(page.locator('#candidates button')).to_have_count(2)
        page.locator('#candidates button').first.click()
        expect(page.locator('#nav-status')).to_have_text('preview')
        page.locator('#nav-start').click()
        expect(page.locator('#nav-status')).to_have_text('navigating')
        for point in ['middle','corner','north','end','end']:
            with page.expect_response(lambda r:'/mock-position' in r.url and r.request.method=='POST'):
                page.locator('[data-fix='+point+']').click()
        expect(page.locator('#nav-status')).to_have_text('near_destination')
        expect(page.locator('#nav-message')).to_contain_text('确认')
        page.locator('#nav-stop').click()
        expect(page.locator('#nav-status')).to_have_text('idle')
        page.locator('#map-mode').select_option('amap')
        expect(page.locator('#map-notice')).to_contain_text('未配置')
        page.locator('#search-button').click()
        expect(page.locator('#nav-error')).to_contain_text('AMAP_API_KEY')
        # Stub only backend reception; exercise real browser watchPosition with emulated GPS.
        captured=[]
        def receive(route):
            captured.append(route.request.post_data_json)
            route.fulfill(status=200,content_type='application/json',body='{}')
        page.route('**/api/navigation/position',receive)
        page.locator('#browser-start').click()
        expect(page.locator('#browser-info')).to_contain_text('正在接收浏览器位置')
        assert captured and captured[0]['coordinate_system']=='WGS84'
        assert captured[0]['source']=='browser'
        assert captured[0]['longitude']==116.4
        page.locator('#browser-stop').click()
        expect(page.locator('#browser-info')).to_have_text('定位已停止')
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors,errors
        page.locator('#map-mode').select_option('mock')
        expect(page.locator('#map-mode')).to_have_value('mock')
        browser.close()
    print('PASS: navigation candidate confirmation, mock course, Amap missing key, browser emulated location bridge, stop and mobile layout')


if __name__=='__main__': main()
