import asyncio
import os
from pyrogram import Client
from dotenv import load_dotenv
from music_module import register_music_handlers, setup_music

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ASSISTANT_SESSION = os.getenv("ASSISTANT_SESSION")

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

async def main():
    calls = await setup_music(
        bot_client=app,
        api_id=API_ID,
        api_hash=API_HASH,
        assistant_session=ASSISTANT_SESSION
    )
    if calls:
        register_music_handlers(app)
    else:
        print("Music feature disabled")

    await app.start()
    print("Bot started!")
    await asyncio.Event().wait()  # simpler than asyncio.sleep(forever)

if __name__ == "__main__":
    asyncio.run(main())
