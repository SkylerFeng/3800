"""Optional real browser check; run against a server on 127.0.0.1:8000."""
import io
import os
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright, expect


def main():
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('CHROME_PATH','/usr/bin/google-chrome'),headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1100})
        page.on('pageerror',lambda error: errors.append(str(error)))
        base=os.environ.get('BROWSER_BASE_URL','http://127.0.0.1:8000').rstrip('/')
        page.goto(base)
        expect(page.locator('#connection')).to_have_text('本机服务已连接')
        initial=page.request.get(base+'/api/state').json()
        assert initial['vision']['provider']=='mock', 'Run this offline test on a dedicated mock server, not the configured real provider.'
        page.locator('#camera-mode').select_option('mock')
        expect(page.locator('#image-label')).to_contain_text('模拟')
        buf=io.BytesIO();Image.new('RGB',(100,80),'green').save(buf,format='PNG')
        page.locator('#image-file').set_input_files({'name':'test.png','mimeType':'image/png','buffer':buf.getvalue()})
        expect(page.locator('#image-label')).to_have_text('本地上传 · 静态素材')
        page.locator('#question').fill('杯子在哪里？')
        page.locator('#ask').click()
        expect(page.locator('#task-status')).to_contain_text('completed')
        expect(page.locator('#answer')).to_contain_text('模拟回答，未执行真实图像识别')
        page.locator('[data-scenario=near]').click()
        expect(page.locator('#warning-level')).to_have_text('critical')
        expect(page.locator('#haptic-event')).to_contain_text('simulated')
        page.locator('[data-scenario=fault]').click()
        expect(page.locator('#distance-info')).to_contain_text('fault')
        expect(page.locator('#warning-level')).to_have_text('unknown')
        page.locator('[data-scenario=no_target]').click()
        expect(page.locator('#distance-info')).to_contain_text('no_target')
        page.locator('[data-scenario=normal]').click()
        expect(page.locator('#warning-level')).to_have_text('normal')
        expect(page.locator('#distance-info')).to_contain_text('stale',timeout=6000)
        page.locator('#vision-mode').select_option('unconfigured')
        expect(page.locator('#vision-mode')).to_have_value('unconfigured')
        page.locator('#ask').click()
        expect(page.locator('#task-status')).to_contain_text('failed')
        page.locator('#vision-mode').select_option('openai_compatible')
        expect(page.locator('#vision-info')).to_contain_text('VISION_API_BASE_URL')
        page.locator('#ask').click()
        expect(page.locator('#task-status')).to_contain_text('failed')
        expect(page.locator('#answer')).to_contain_text('VISION_API_BASE_URL')
        page.locator('#vision-mode').select_option('ollama')
        expect(page.locator('#vision-info')).to_contain_text('OLLAMA_MODEL')
        page.locator('#vision-mode').select_option('mock')
        expect(page.locator('#vision-mode')).to_have_value('mock')
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        Path('artifacts').mkdir(exist_ok=True)
        page.screenshot(path='artifacts/browser-mobile.png',full_page=True)
        page.set_viewport_size({'width':1440,'height':1100})
        page.screenshot(path='artifacts/browser-desktop.png',full_page=True)
        assert not errors,errors
        browser.close()
    print('PASS: upload, mock question, near/normal/no_target/fault/stale, provider failure, mobile layout; no JS errors')


if __name__=='__main__': main()
