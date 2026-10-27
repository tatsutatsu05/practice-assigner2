import os

from playwright.sync_api import sync_playwright

URL = os.environ["STREAMLIT_APP_URL"]

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(URL, timeout=60_000)

    # スリープ中なら「Yes, get this app back up!」ボタンが出る
    wake_button = page.get_by_role("button", name="get this app back up")
    try:
        wake_button.wait_for(timeout=15_000)
        wake_button.click()
        print("スリープ中だったので起こしました")
    except Exception:
        print("ボタンなし(すでに起きている)")

    # アプリが立ち上がって、アクセスとして数えられるまで少し待つ
    page.wait_for_timeout(60_000)
    browser.close()
