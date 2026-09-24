import os
import re
import time
from urllib.parse import urlparse
import cloudscraper
from bs4 import BeautifulSoup
from ebooklib import epub
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

# ============================================================
# CẤU HÌNH BOT & FLASK WEBHOOK
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN_TRUYENFULL") or os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
  print("❌ Lỗi: Thiếu BOT_TOKEN!")

app = Flask(__name__)

# Khởi tạo Application của python-telegram-bot
application = (
    Application.builder().token(BOT_TOKEN).read_timeout(30).connect_timeout(30).build()
)

scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "desktop": True}
)


def get_soup(url):
  try:
    response = scraper.get(url, timeout=35)
    if response.status_code == 200:
      return BeautifulSoup(response.text, "lxml")
  except Exception as e:
    print(f"Lỗi tải {url}: {e}")
  return None


def download_chapter(url):
  soup = get_soup(url)
  if not soup:
    return None

  content = (
      soup.select_one(".chapter-content")
      or soup.select_one("#chapter-c")
      or soup.select_one(".chapter-c")
      or soup.select_one(".entry-content")
      or soup.select_one(".post-content")
  )

  if not content:
    return None

  for tag in content.find_all(
      ["script", "style", "div", "ins", "iframe", "button"]
  ):
    tag.decompose()

  return str(content)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if not update.message or not update.message.text:
    return

  url = re.findall(r"https?://[^\s]+", update.message.text)
  if not url:
    return

  status = await update.message.reply_text(
      "⏳ Đang kết nối và quét danh sách chương chuẩn xác..."
  )

  story_url = url[0].strip()
  main_soup = get_soup(story_url)
  if not main_soup:
    await status.edit_text(
        "❌ Không thể kết nối tới trang truyện. Web có thể đang chặn hoặc sai"
        " link."
    )
    return

  title_el = main_soup.select_one("h1") or main_soup.select_one(".title")
  title = title_el.get_text().strip() if title_el else "Truyện"
  if "|" in title:
    title = title.split("|")[0].strip()

  cover_url = None
  img_tag = (
      main_soup.select_one(".book img")
      or main_soup.select_one(".truyen-info img")
      or main_soup.select_one("article img")
  )
  if img_tag and img_tag.get("src"):
    cover_url = img_tag["src"]

  parsed_url = urlparse(story_url)
  base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
  story_path = parsed_url.path.rstrip("/")

  links = []
  current_page_url = story_url
  page_num = 1

  while current_page_url:
    soup = get_soup(current_page_url)
    if not soup:
      break

    chapter_tags = soup.select(
        "#list-chapter a, .list-chapter a, .chapter-list a, .zaraz-list a"
    )
    if not chapter_tags:
      break

    new_chapters_in_page = 0
    for a in chapter_tags:
      href = a.get("href", "")
      if href:
        full_url = href if href.startswith("http") else base_domain + href
        if story_path not in full_url:
          continue

        text = a.get_text().strip()
        if text and not any(l["url"] == full_url for l in links):
          links.append({"name": text, "url": full_url})
          new_chapters_in_page += 1

    if new_chapters_in_page == 0:
      break

    pagination_links = soup.select(".pagination a, .pages a")
    next_url = None
    for p_link in pagination_links:
      text_p = p_link.get_text().strip()
      if (
          "Trang sau" in text_p
          or ">" in text_p
          or str(page_num + 1) == text_p
      ):
        next_url = p_link.get("href")
        break

    if next_url:
      next_full_url = (
          next_url if next_url.startswith("http") else base_domain + next_url
      )
      if next_full_url == current_page_url:
        break
      current_page_url = next_full_url
      page_num += 1
    else:
      if page_num < 40:
        if story_url.endswith("/"):
          guessed_url = f"{story_url}trang-{page_num + 1}/"
        else:
          guessed_url = f"{story_url}/trang-{page_num + 1}/"
        current_page_url = guessed_url
        page_num += 1
      else:
        break
    time.sleep(0.2)

  if not links:
    await status.edit_text("❌ Không tìm thấy chương nào hoặc link không hợp lệ.")
    return

  await status.edit_text(
      f"📚 {title}\n✅ Quét thành công {len(links)} chương chuẩn xác. Đang tải"
      " nội dung..."
  )

  book = epub.EpubBook()
  book.set_identifier("truyen_" + re.sub(r"\W+", "", title))
  book.set_title(title)
  book.set_language("vi")

  if cover_url:
    try:
      full_cover_url = (
          cover_url if cover_url.startswith("http") else base_domain + cover_url
      )
      img_data = scraper.get(full_cover_url, timeout=15).content
      book.set_cover("cover.jpg", img_data)
    except Exception as e:
      print(f"Lỗi tải ảnh bìa: {e}")

  chapters_list = []
  success_count = 0

  for i, item in enumerate(links):
    content = download_chapter(item["url"])
    if content:
      chap = epub.EpubHtml(title=item["name"], file_name=f"chap_{i+1}.xhtml")
      chap.content = f"<h2>{item['name']}</h2>{content}"
      book.add_item(chap)
      chapters_list.append(chap)
      success_count += 1

    if i % 15 == 0 or i == len(links) - 1:
      pct = int((i / len(links)) * 100)
      try:
        await status.edit_text(
            f"📚 {title}\n⏳ Đang tải: {pct}%\n({i+1}/{len(links)})"
        )
      except:
        pass
    time.sleep(0.1)

  if success_count == 0:
    await status.edit_text("❌ Tải thất bại do trang web chặn toàn bộ nội dung.")
    return

  book.toc = tuple(chapters_list)
  book.spine = ["nav"] + chapters_list

  book.add_item(epub.EpubNcx())
  book.add_item(epub.EpubNav())

  safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Truyen"
  file_name = f"{safe_title}.epub"
  epub.write_epub(file_name, book)

  await status.edit_text("⬆️ Đang gửi file EPUB qua Telegram...")
  with open(file_name, "rb") as f:
    await update.message.reply_document(
        document=f,
        caption=(
            f"✅ Xong: {title}\n📖 {success_count}/{len(links)} chương chuẩn"
            " xác + Ảnh bìa & Mục lục đầy đủ!"
        ),
    )

  await status.delete()
  if os.path.exists(file_name):
    os.remove(file_name)


# Đăng ký handler
application.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
)


# ============================================================
# FLASK WEBHOOK ROUTES (CÓ CHỐNG LẶP REQUEST)
# ============================================================
processed_updates = set()


@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
  if request.headers.get("content-type") == "application/json":
    json_data = request.get_json(force=True)
    update = Update.de_json(json_data, application.bot)

    if not update:
      return "OK", 200

    # Chặn đứng các request bị Telegram gửi lại (retry) do xử lý lâu
    if update.update_id in processed_updates:
      return "OK", 200

    processed_updates.add(update.update_id)
    if len(processed_updates) > 100:
      processed_updates.pop()

    # Chạy xử lý thông qua vòng lặp sự kiện bất đồng bộ mượt mà
    import asyncio

    async def run_async():
      await application.initialize()
      await application.process_update(update)

    asyncio.run(run_async())
    return "OK", 200
  return "Invalid request", 403


@app.route("/")
def index():
  render_url = os.getenv("RENDER_EXTERNAL_URL")
  if render_url:
    webhook_url = f"{render_url}/{BOT_TOKEN}"
    import requests

    try:
      requests.get(
          f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}",
          timeout=10,
      )
    except Exception as e:
      print(f"Lỗi set webhook tự động: {e}")
    return f"Bot TruyenFull Webhook đang chạy! Trỏ tới: {webhook_url}", 200
  return "Bot TruyenFull đang hoạt động!", 200


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)

