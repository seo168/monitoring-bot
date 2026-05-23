import os, json, time
import gspread, pyotp
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright

load_dotenv()

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") 
print("JSON START:", raw[:30]) 
print("JSON LINES:", raw.count("\n"))
CREDS_JSON = json.loads(raw)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_info(CREDS_JSON, scopes=SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(GOOGLE_SHEET_ID).worksheet("thirdparty_rate")


HEADER = [
    "日期","国家","统一三方",
    "代收金额","代收占比","代收笔数",
    "代付金额","代付占比","代付笔数",
    "合计金额","合计笔数",
    "代收费率","代收手续费",
    "代付费率","代付手续费",
    "合计手续费","手续费占比",
    "昨日代收","代收对比",
    "昨日代付","代付对比",
    "总占比","备注"
]


def get_otp(platform):
    secret_env = platform.get("otp_secret_env")
    if not secret_env:
        return None

    secret = os.getenv(secret_env)
    if not secret:
        return None

    return pyotp.TOTP(secret).now()


def login(page, platform):
    print(f"LOGIN: {platform['name']}")

    page.goto(platform["url"], timeout=120000)
    page.wait_for_load_state("networkidle", timeout=60000)
    page.wait_for_timeout(5000)

    print("PAGE TITLE:", page.title())
    print("CURRENT URL:", page.url)
    print("INPUT COUNT:", page.locator("input").count())

    inputs = page.locator("input")

    if inputs.count() < 2:
        page.screenshot(path=f"{platform['name']}_login_error.png", full_page=True)
        raise Exception("Login input not found")

    inputs.nth(0).fill(platform["user"])
    inputs.nth(1).fill(os.getenv(platform["password_env"]))

    otp = get_otp(platform)

    if otp:
        print(f"OTP READY: {platform['name']}")

        for i in range(inputs.count()):
            placeholder = (inputs.nth(i).get_attribute("placeholder") or "").lower()
            name = (inputs.nth(i).get_attribute("name") or "").lower()

            if "otp" in placeholder or "验证码" in placeholder or "code" in name:
                inputs.nth(i).fill(otp)
                break

    login_btn = page.locator("button").filter(has_text="登录")

    if login_btn.count() > 0:
        login_btn.first.click()
    else:
        page.keyboard.press("Enter")

    page.wait_for_timeout(6000)


def scrape_india(page, platform):
    login(page, platform)

    rows = []
    today = time.strftime("%Y-%m-%d")

    # TODO: sesuaikan kalau menu 三方量/费率 perlu diklik dulu
    page.wait_for_timeout(5000)

    table_rows = page.locator("table tbody tr")
    count = table_rows.count()

    print(f"{platform['name']} table rows: {count}")

    temp = []
    total_receive = 0
    total_payout = 0

    for i in range(count):
        cols = table_rows.nth(i).locator("td")

        try:
            provider = cols.nth(0).inner_text().strip()
            receive_amount = to_num(cols.nth(1).inner_text())
            receive_count = int(to_num(cols.nth(2).inner_text()))
            payout_amount = to_num(cols.nth(3).inner_text())
            payout_count = int(to_num(cols.nth(4).inner_text()))
            receive_rate = cols.nth(5).inner_text().strip() or "0%"
            payout_rate = cols.nth(6).inner_text().strip() or "0%"

            total_receive += receive_amount
            total_payout += payout_amount

            temp.append({
                "provider": provider,
                "receive_amount": receive_amount,
                "receive_count": receive_count,
                "payout_amount": payout_amount,
                "payout_count": payout_count,
                "receive_rate": receive_rate,
                "payout_rate": payout_rate
            })

        except Exception as e:
            print(f"SKIP ROW {i}: {e}")

    grand_total = total_receive + total_payout

    for x in temp:
        receive_fee = x["receive_amount"] * percent(x["receive_rate"])
        payout_fee = x["payout_amount"] * percent(x["payout_rate"])
        total_fee = receive_fee + payout_fee
        total_amount = x["receive_amount"] + x["payout_amount"]
        total_count = x["receive_count"] + x["payout_count"]

        rows.append([
            today,
            platform["country"],
            x["provider"],

            int(x["receive_amount"]),
            pct(x["receive_amount"], total_receive),
            x["receive_count"],

            int(x["payout_amount"]),
            pct(x["payout_amount"], total_payout),
            x["payout_count"],

            int(total_amount),
            total_count,

            x["receive_rate"],
            int(receive_fee),

            x["payout_rate"],
            int(payout_fee),

            int(total_fee),
            pct(total_fee, total_amount),

            0,
            "0%",

            0,
            "0%",

            pct(total_amount, grand_total),
            platform["name"]
        ])

    return rows


def scrape_bangladesh(page, platform):
    # sementara pakai struktur sama.
    # nanti kalau Bdspin table beda, bagian ini yang disesuaikan.
    return scrape_india(page, platform)


def to_num(v):
    return float(str(v).replace(",", "").replace("%", "").strip() or 0)


def percent(v):
    return to_num(v) / 100


def pct(a, b):
    if not b:
        return "0%"
    return f"{(a / b) * 100:.2f}%"


def update_sheet(rows):
    sheet.clear()
    sheet.append_row(HEADER)

    if rows:
        sheet.append_rows(rows, value_input_option="USER_ENTERED")

    print(f"SHEET UPDATED: {len(rows)} rows")


def main():
    with open("platforms.json", "r", encoding="utf-8") as f:
        platforms = json.load(f)

    all_rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )

        for platform in platforms:
            try:
                page = browser.new_page()

                if platform["type"] == "india":
                    rows = scrape_india(page, platform)
                elif platform["type"] == "bangladesh":
                    rows = scrape_bangladesh(page, platform)
                else:
                    rows = []

                all_rows.extend(rows)
                page.close()

            except Exception as e:
                print(f"ERROR {platform['name']}: {e}")

        browser.close()

    update_sheet(all_rows)


if __name__ == "__main__":
    main()
