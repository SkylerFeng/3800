"""Optional browser check for the offline demo, with eSpeak NG enabled."""
import os
import time

from playwright.sync_api import expect, sync_playwright


def wait_for_media(page, selector, expression, timeout=5):
    audio = page.locator(selector)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if audio.evaluate(expression):
            return
        page.wait_for_timeout(50)
    raise AssertionError(f'Media did not reach expected state: {selector}')


def main():
    base = os.environ.get('BROWSER_BASE_URL', 'http://127.0.0.1:8002').rstrip('/')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get('CHROME_PATH', '/usr/bin/google-chrome'),
            headless=True, args=['--no-sandbox', '--autoplay-policy=no-user-gesture-required'],
        )
        page = browser.new_page(viewport={'width': 1200, 'height': 820})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        initial = page.request.get(base + '/api/state').json()
        assert initial['vision']['provider'] == 'mock'
        page.goto(base + '/demo')
        expect(page.get_by_role('heading', name='视障辅助眼镜演示')).to_be_visible()
        page.get_by_role('link', name='打开眼镜控制台').click()
        page.locator('#cancel').click()
        expect(page.locator('#audio-info')).to_contain_text('已取消')
        page.get_by_role('button', name='颜色示例').click()
        expect(page.locator('#question')).to_have_value('杯子是什么颜色？')
        expect(page.locator('#image-label')).to_contain_text('模拟')
        page.locator('#ask').click()
        expect(page.locator('#answer')).to_contain_text('杯子是蓝色')
        expect(page.locator('#answer-audio')).to_be_visible()
        wait_for_media(page, '#answer-audio', 'audio => audio.readyState >= 2')
        page.locator('#answer-audio').evaluate('audio => audio.play()')
        wait_for_media(page, '#answer-audio', 'audio => audio.currentTime > 0')
        page.locator('#obstacle-tone').check()
        page.locator('[data-scenario=near]').click()
        expect(page.locator('#warning-level')).to_have_text('快响')
        expect(page.locator('#answer-audio')).to_be_hidden()
        page.locator('[data-scenario=no_target]').click()
        expect(page.locator('#warning-level')).to_have_text('未知')
        page.get_by_role('button', name='目的地示例').click()
        page.locator('#use-destination').click()
        expect(page.locator('#destination-query')).to_have_value('图书馆')
        page.locator('#map-mode').select_option('mock')
        page.locator('#mock-start').click()
        page.locator('#search-button').click()
        page.locator('#candidates button').first.click()
        expect(page.locator('#nav-status')).to_have_text('preview')
        page.locator('#nav-start').click()
        expect(page.locator('#nav-speak')).to_be_enabled()
        page.locator('#nav-speak').click()
        wait_for_media(page, '#nav-audio', 'audio => audio.currentTime > 0')
        page.locator('#nav-pause').click()
        expect(page.locator('#nav-audio')).to_be_hidden()
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        browser.close()
    print('PASS: offline demo, WAV playback, obstacle priority, destination draft, route speech, pause and mobile layout')


if __name__ == '__main__':
    main()
