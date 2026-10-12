from pathlib import Path
from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo
from core.video_metadata import VideoMetadata

class TelegramDelivery:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def send_files(self, chat_id: int, files: list[tuple[str, Path]], caption: str | None = None, *, video_metadata: VideoMetadata | None = None):
        """Send photos/videos. Telegram albums are capped at 10 items, so chunk them."""
        if not files:
            raise ValueError("No media files to deliver")

        if video_metadata is not None and (len(files) != 1 or files[0][0] != "video"):
            raise ValueError("Video metadata requires exactly one video")

        if len(files) == 1:
            kind, path = files[0]
            upload = FSInputFile(path)
            if kind == "photo":
                msg = await self.bot.send_photo(chat_id, upload, caption=caption)
                return [msg]
            msg = await self.bot.send_video(chat_id, upload, caption=caption, supports_streaming=True, **(video_metadata.telegram_kwargs() if video_metadata else {}))
            return [msg]

        sent = []
        for start in range(0, len(files), 10):
            chunk = files[start:start + 10]
            media = []
            for idx, (kind, path) in enumerate(chunk):
                upload = FSInputFile(path)
                item_caption = caption if start == 0 and idx == 0 else None
                if kind == "photo":
                    media.append(InputMediaPhoto(media=upload, caption=item_caption))
                else:
                    media.append(InputMediaVideo(media=upload, caption=item_caption, supports_streaming=True))
            sent.extend(await self.bot.send_media_group(chat_id, media))
        return sent
