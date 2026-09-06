import asyncio
import aiohttp
import os
import uuid
import subprocess
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.types import Message, FSInputFile
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import logging

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
LOG_CHAT_ID = os.getenv("LOG_CHAT_ID")

INSTAGRAM_API = "https://api.delirius.online/download/instagram?url="
TIKTOK_API = "https://api.delirius.online/download/tiktok?url="
YOUTUBE_API = "https://api.delirius.online/download/ytmp4?url="

# Basic console logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

# Convert LOG_CHAT_ID to int if provided
if LOG_CHAT_ID:
    try:
        LOG_CHAT_ID = int(LOG_CHAT_ID)
    except ValueError:
        logger.warning("LOG_CHAT_ID environment variable is not a valid integer. Logging to Telegram disabled.")
        LOG_CHAT_ID = None


dp = Dispatcher()

@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "👋 <b>Instagram / TikTok / YouTube Downloader</b>\n\n"
        "Send an Instagram, TikTok, or YouTube link and I will download the media."
    )

# ✅ Updated to support YouTube
async def fetch_data(url: str):
    if "tiktok" in url or "tiktokcdn" in url or "vm.tiktok" in url:
        api = f"{TIKTOK_API}{url}"

    elif "youtube.com" in url or "youtu.be" in url:
        api = f"{YOUTUBE_API}{url}&format=720"

    else:
        api = f"{INSTAGRAM_API}{url}"

    async with aiohttp.ClientSession() as session:
        async with session.get(api) as resp:
            if resp.status != 200:
                return {"status": False}
            return await resp.json()

async def download_file(url):
    filename = f"/tmp/{uuid.uuid4().hex}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise ValueError(f"Download failed (status={resp.status}) for {url}")
            with open(filename, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 256):
                    if not chunk:
                        continue
                    f.write(chunk)

    try:
        size = os.path.getsize(filename)
    except OSError:
        size = 0

    if size == 0:
        if os.path.exists(filename):
            os.remove(filename)
        raise ValueError("Downloaded file is empty")

    return filename

def create_thumbnail(video_path):
    thumb = f"{video_path}.jpg"
    command = [
        "ffmpeg",
        "-ss", "00:00:01",
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", "2",
        thumb
    ]
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return thumb

# ✅ Updated to support YouTube response
def parse_media(api_json, original_url: str):
    out = []

    # 🎥 YouTube handling
    if "youtube.com" in original_url or "youtu.be" in original_url:
        data = api_json.get("data", {})
        download_url = data.get("download")
        if download_url:
            out.append({"url": download_url, "type": "video"})
        return out

    data = api_json.get("data")

    if isinstance(data, list):
        for m in data:
            murl = m.get("url")
            mtype = m.get("type", "document")
            if murl:
                out.append({"url": murl, "type": mtype})
        return out

    if isinstance(data, dict):
        meta = data.get("meta", {})
        media = meta.get("media", [])

        for m in media:
            mtype = m.get("type")

            if mtype == "video":
                url = m.get("org") or m.get("hd") or m.get("wm")
                if url:
                    out.append({"url": url, "type": "video"})

            elif mtype == "image":
                images = m.get("images", [])
                for img in images:
                    if img:
                        out.append({"url": img, "type": "image"})

                audio = m.get("audio")
                if audio:
                    out.append({"url": audio, "type": "audio"})

            else:
                for k in ("url", "org", "hd", "wm"):
                    if m.get(k):
                        out.append({"url": m.get(k), "type": mtype or "document"})
                        break

        return out

    if isinstance(api_json, dict) and api_json.get("url"):
        out.append({"url": api_json.get("url"), "type": "document"})

    return out

@dp.message()
async def downloader(message: Message):
    url = (message.text or "").strip()

    if not url:
        await message.reply("❌ Please send a valid link.")
        return

    # ✅ Updated validation (added YouTube)
    if ("instagram.com" not in url) and ("tiktok" not in url) and ("youtube.com" not in url) and ("youtu.be" not in url):
        await message.reply("❌ Please send a valid Instagram, TikTok, or YouTube link.")
        return

    status = await message.reply("⏳ Fetching media...")

    try:
        data = await fetch_data(url)

        if not data.get("status"):
            await status.edit_text("❌ Failed to fetch media.")
            return

        media_list = parse_media(data, url)

        if not media_list:
            await status.edit_text("❌ No media found.")
            return

        await status.edit_text("📥 Downloading...")

        downloaded_files = []

        for media in media_list:
            murl = media.get("url")
            mtype = media.get("type", "document")

            if not murl:
                continue

            try:
                file_path = await download_file(murl)
                downloaded_files.append((file_path, mtype))
            except Exception as e:
                await message.reply(f"⚠️ Skipped a file (download failed): {e}")

        if not downloaded_files:
            await status.edit_text("❌ Nothing downloaded.")
            return

        # Inform log group (if configured)
        try:
            if LOG_CHAT_ID:
                user = message.from_user
                username = f"@{user.username}" if getattr(user, 'username', None) else ''
                first = getattr(user, 'first_name', '') or ''
                last = getattr(user, 'last_name', '') or ''
                fullname = (first + ' ' + last).strip() or 'Unknown'

                total_files = len(downloaded_files)
                details_lines = []
                total_size = 0
                for fp, mtype in downloaded_files:
                    try:
                        sz = os.path.getsize(fp)
                    except OSError:
                        sz = 0
                    total_size += sz
                    details_lines.append(f"- {os.path.basename(fp)} ({mtype}) — {sz // 1024} KB")

                msg = (
                    f"📥 <b>Download</b>\n"
                    f"User: <b>{fullname}</b> {username}\n"
                    f"User ID: <code>{user.id}</code>\n"
                    f"URL: {url}\n"
                    f"Files: {total_files}\n"
                    f"Total size: {total_size // 1024} KB\n"
                    f"Details:\n{chr(10).join(details_lines)}"
                )

                await bot.send_message(LOG_CHAT_ID, msg)
                logger.info(f"Logged download to chat {LOG_CHAT_ID}: user_id={user.id} url={url} files={total_files}")
        except Exception as e:
            logger.exception(f"Failed to send log message to LOG_CHAT_ID: {e}")

        await status.edit_text("⬆ Uploading...")

        for file_path, media_type in downloaded_files:
            file = FSInputFile(file_path)

            try:
                if media_type == "video":
                    thumb = create_thumbnail(file_path)
                    thumb_file = FSInputFile(thumb) if os.path.exists(thumb) else None

                    await message.answer_video(
                        video=file,
                        thumbnail=thumb_file,
                        supports_streaming=True
                    )

                    if thumb_file and os.path.exists(thumb):
                        os.remove(thumb)

                elif media_type == "image":
                    await message.answer_photo(file)

                elif media_type == "audio":
                    await message.answer_audio(file)

                else:
                    await message.answer_document(file)

            except Exception as e:
                await message.reply(f"❌ Telegram send failed: {e}")

            if os.path.exists(file_path):
                os.remove(file_path)

        await status.delete()

    except Exception as e:
        await message.reply(f"❌ Error: {e}")

async def main():
    logger.info("🚀 Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
