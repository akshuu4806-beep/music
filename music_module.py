"""
╔══════════════════════════════════════════════════════════════════╗
║              MUSIC MODULE - Voice Chat Player                     ║
║                   (Assistant/Userbot Version)                     ║
║                                                                   ║
║  Libraries needed:                                                ║
║    pip install pytgcalls py-tgcalls                              ║
║    pip install yt-dlp                                             ║
║    pip install pyrogram motor python-dotenv                       ║
║                                                                   ║
║  .env mein ZAROOR add karo:                                       ║
║    API_ID=your_api_id                                             ║
║    API_HASH=your_api_hash                                         ║
║    ASSISTANT_SESSION=your_string_session  ← IMPORTANT            ║
║    (String session banane ke liye: python -c "from pyrogram      ║
║     import Client; Client(':memory:', API_ID,                    ║
║     API_HASH).run(Client.export_session_string)")                ║
╚══════════════════════════════════════════════════════════════════╝

MAIN BOT FILE MEIN ADD KARO:
    from music_module import register_music_handlers, setup_music
    
    async def main():
        ...
        calls = await setup_music(app, API_ID, API_HASH)
        if calls:
            register_music_handlers(app)
        await app.start()
        await idle()
"""

import asyncio
import os
import random
import re
from collections import defaultdict, deque
from typing import Optional

import yt_dlp
from pyrogram import Client, enums, filters
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ── pytgcalls import ──────────────────────────────────────────────
try:
    from pytgcalls import PyTgCalls, idle
    from pytgcalls.types import AudioPiped, StreamAudioEnded
    from pytgcalls.types.input_stream import AudioParameters
    PYTGCALLS_AVAILABLE = True
except ImportError:
    PYTGCALLS_AVAILABLE = False
    print("⚠️  pytgcalls not installed. Run: pip install pytgcalls py-tgcalls")

# ── Spotify (optional) ────────────────────────────────────────────
try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    _sp_id  = os.getenv("SPOTIFY_CLIENT_ID", "")
    _sp_sec = os.getenv("SPOTIFY_CLIENT_SECRET", "")
    if _sp_id and _sp_sec:
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=_sp_id, client_secret=_sp_sec))
        SPOTIFY_AVAILABLE = True
    else:
        SPOTIFY_AVAILABLE = False
        sp = None
except ImportError:
    SPOTIFY_AVAILABLE = False
    sp = None

# ─────────────────────────────────────────────────────────────────
#  Global State
# ─────────────────────────────────────────────────────────────────

# music_queue[chat_id] = deque of track dicts
music_queue: dict[int, deque]         = defaultdict(deque)
currently_playing: dict[int, Optional[dict]] = {}
loop_mode: dict[int, str]             = defaultdict(lambda: "off")
vc_joined: set[int]                   = set()

assistant_client: Optional[Client]    = None
calls: Optional[object]               = None   # PyTgCalls instance
_main_bot_client: Optional[Client]    = None   # reference to main bot for send_message

# ─────────────────────────────────────────────────────────────────
#  Setup
# ─────────────────────────────────────────────────────────────────

async def setup_music(bot_client: Client, api_id: int, api_hash: str,
                      assistant_session: str = None) -> Optional[object]:
    """
    Call this from your main() function (BEFORE app.start()).

    assistant_session : Pyrogram String Session of your ASSISTANT ACCOUNT.
                        Get from env var ASSISTANT_SESSION.
    Returns           : PyTgCalls instance (or None on failure).
    """
    global assistant_client, calls, _main_bot_client

    if not PYTGCALLS_AVAILABLE:
        print("❌ pytgcalls install nahi hai. Music disabled.")
        return None

    session = assistant_session or os.getenv("ASSISTANT_SESSION")
    if not session:
        print(
            "❌ ASSISTANT_SESSION nahi mila!\n"
            "   .env mein add karo: ASSISTANT_SESSION=your_string_session\n"
            "   String session kaise banayein:\n"
            "     python -c \"from pyrogram import Client; "
            "c=Client(':memory:', API_ID, API_HASH); c.run(c.export_session_string())\""
        )
        return None

    _main_bot_client = bot_client

    # Assistant userbot client banao
    assistant_client = Client(
        name="music_assistant",
        api_id=api_id,
        api_hash=api_hash,
        session_string=session,
    )

    calls = PyTgCalls(assistant_client)

    # Stream-end event register karo
    @calls.on_stream_end()
    async def _on_stream_end(_, update):
        if isinstance(update, StreamAudioEnded):
            await _play_next_internal(update.chat_id)

    await assistant_client.start()
    await calls.start()
    print("✅ Music assistant started!")
    return calls


# ─────────────────────────────────────────────────────────────────
#  YT-DLP Helper
# ─────────────────────────────────────────────────────────────────

async def search_youtube(query: str) -> Optional[dict]:
    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "default_search": "ytsearch1:",
        "source_address": "0.0.0.0",
    }
    loop = asyncio.get_event_loop()

    def _extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(query, download=False)
                if "entries" in info:
                    info = info["entries"][0]
                return {
                    "title":       info.get("title", "Unknown"),
                    "url":         info.get("webpage_url", ""),
                    "stream_url":  info.get("url", ""),
                    "duration":    info.get("duration", 0),
                    "thumbnail":   info.get("thumbnail", ""),
                    "uploader":    info.get("uploader", "Unknown"),
                }
            except Exception as e:
                print(f"YT-DLP error: {e}")
                return None

    return await loop.run_in_executor(None, _extract)


async def _refresh_stream_url(url: str) -> Optional[str]:
    """YouTube stream URLs expire hoti hain — fresh lo."""
    ydl_opts = {"format": "bestaudio/best", "quiet": True, "no_warnings": True}
    loop = asyncio.get_event_loop()

    def _extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                if "entries" in info:
                    info = info["entries"][0]
                return info.get("url", "")
            except Exception:
                return None

    return await loop.run_in_executor(None, _extract)


# ─────────────────────────────────────────────────────────────────
#  Spotify Helper
# ─────────────────────────────────────────────────────────────────

async def get_spotify_info(spotify_url: str):
    """
    Returns:
        str   → single track name
        list  → list of track names (playlist)
        None  → error
    """
    if not SPOTIFY_AVAILABLE or not sp:
        return None
    loop = asyncio.get_event_loop()

    def _fetch():
        try:
            if "track" in spotify_url:
                t = sp.track(spotify_url)
                artists = ", ".join(a["name"] for a in t["artists"])
                return f"{t['name']} {artists}"
            elif "playlist" in spotify_url:
                results = sp.playlist_tracks(spotify_url)
                names = []
                for item in (results.get("items") or [])[:15]:
                    t = item.get("track")
                    if not t:
                        continue
                    artists = ", ".join(a["name"] for a in t["artists"])
                    names.append(f"{t['name']} {artists}")
                return names
            return None
        except Exception as e:
            print(f"Spotify error: {e}")
            return None

    return await loop.run_in_executor(None, _fetch)


# ─────────────────────────────────────────────────────────────────
#  Player Core
# ─────────────────────────────────────────────────────────────────

def _fmt_dur(secs: int) -> str:
    if not secs:
        return "🔴 Live"
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _control_kb(chat_id: int) -> InlineKeyboardMarkup:
    lm = loop_mode.get(chat_id, "off")
    loop_label = {"off": "➡️ Loop: Off", "song": "🔁 Loop: Song", "queue": "🔄 Loop: Queue"}[lm]
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏸ Pause",  callback_data=f"mus:pause:{chat_id}"),
            InlineKeyboardButton("▶️ Resume", callback_data=f"mus:resume:{chat_id}"),
        ],
        [
            InlineKeyboardButton("⏭ Skip",   callback_data=f"mus:skip:{chat_id}"),
            InlineKeyboardButton("⏹ Stop",   callback_data=f"mus:stop:{chat_id}"),
        ],
        [
            InlineKeyboardButton(loop_label,  callback_data=f"mus:loop:{chat_id}"),
            InlineKeyboardButton("📋 Queue",  callback_data=f"mus:queue:{chat_id}"),
        ],
        [
            InlineKeyboardButton("🔉 Vol-",   callback_data=f"mus:vol_down:{chat_id}"),
            InlineKeyboardButton("🔊 Vol+",   callback_data=f"mus:vol_up:{chat_id}"),
        ],
    ])


async def _play_next_internal(chat_id: int):
    """Queue se next song play karo (internal use)."""
    global currently_playing

    if not calls:
        return

    queue = music_queue[chat_id]

    # Loop: song — same track dobara
    if loop_mode[chat_id] == "song" and currently_playing.get(chat_id):
        track = currently_playing[chat_id]
    elif queue:
        track = queue.popleft()
        if loop_mode[chat_id] == "queue":
            queue.append(track)
    else:
        # Queue khatam
        currently_playing[chat_id] = None
        vc_joined.discard(chat_id)
        try:
            await calls.leave_group_call(chat_id)
        except Exception:
            pass
        if _main_bot_client:
            try:
                await _main_bot_client.send_message(
                    chat_id,
                    "✅ Queue khatam ho gayi! VC se nikal gaya.\n"
                    "Naya song sunne ke liye `/play` karo! 🎵"
                )
            except Exception:
                pass
        return

    currently_playing[chat_id] = track

    # Fresh stream URL
    stream_url = await _refresh_stream_url(track["url"]) or track.get("stream_url", "")
    if not stream_url:
        if _main_bot_client:
            try:
                await _main_bot_client.send_message(
                    chat_id,
                    f"❌ **Stream URL nahi mili!**\n"
                    f"Song: `{track['title']}`\n"
                    f"Shayad YouTube ne block kar diya. Skip karke next try kar raha hoon..."
                )
            except Exception:
                pass
        currently_playing[chat_id] = None
        await _play_next_internal(chat_id)
        return

    try:
        if chat_id not in vc_joined:
            await calls.join_group_call(
                chat_id,
                AudioPiped(stream_url, AudioParameters.from_quality("HIGH")),
            )
            vc_joined.add(chat_id)
        else:
            await calls.change_stream(
                chat_id,
                AudioPiped(stream_url, AudioParameters.from_quality("HIGH")),
            )

        if _main_bot_client:
            text = (
                f"🎵 **Now Playing**\n\n"
                f"**{track['title']}**\n"
                f"⏱ Duration: {_fmt_dur(track.get('duration', 0))}\n"
                f"👤 Requested by: {track['requested_by']}\n"
                f"📋 Queue mein: {len(queue)} songs baki"
            )
            await _main_bot_client.send_message(
                chat_id, text,
                reply_markup=_control_kb(chat_id),
                parse_mode=enums.ParseMode.MARKDOWN,
            )

    except Exception as e:
        currently_playing[chat_id] = None
        vc_joined.discard(chat_id)
        err_msg = str(e)

        # Common errors ke liye Hinglish messages
        if "not in voice chat" in err_msg.lower() or "groupcall_forbidden" in err_msg.lower():
            friendly = (
                "❌ **VC mein join nahi ho saka!**\n\n"
                "Sambhavit karan:\n"
                "• Group mein Voice Chat enable nahi hai\n"
                "• Assistant account group mein nahi hai\n"
                "• Bot ko VC permission nahi mili\n\n"
                "**Fix:** Group mein Voice Chat start karo, phir `/play` karo."
            )
        elif "peer_id_invalid" in err_msg.lower():
            friendly = (
                "❌ **Chat nahi mila!**\n"
                "Assistant ko pehle is group mein add karo."
            )
        elif "forbidden" in err_msg.lower():
            friendly = (
                "❌ **Permission denied!**\n"
                "Assistant account ko group mein admin banana padega ya invite karna padega."
            )
        else:
            friendly = (
                f"❌ **Play karne mein error aaya!**\n\n"
                f"Song: `{track['title']}`\n"
                f"Error: `{err_msg[:200]}`\n\n"
                f"Next song try kar raha hoon..."
            )

        if _main_bot_client:
            try:
                await _main_bot_client.send_message(
                    chat_id, friendly,
                    parse_mode=enums.ParseMode.MARKDOWN
                )
            except Exception:
                pass

        # Next song try karo agar error recoverable hai
        if queue and "forbidden" not in err_msg.lower():
            await asyncio.sleep(1)
            await _play_next_internal(chat_id)


# ─────────────────────────────────────────────────────────────────
#  Command Handlers
# ─────────────────────────────────────────────────────────────────

async def play_cmd(client: Client, message: Message):
    """/play <song name ya URL>"""
    chat_id = message.chat.id

    # Group check
    if message.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return await message.reply_text(
            "❌ **Yeh command sirf groups mein kaam karta hai!**\n"
            "Kisi group mein ja aur wahan try karo. 😊"
        )

    # pytgcalls check
    if not PYTGCALLS_AVAILABLE:
        return await message.reply_text(
            "❌ **Music feature install nahi hai!**\n\n"
            "Admin se kaho yeh commands run kare:\n"
            "```\npip install pytgcalls py-tgcalls yt-dlp\n```"
        )

    # Assistant check
    if not calls or not assistant_client:
        return await message.reply_text(
            "❌ **Music assistant setup nahi hua!**\n\n"
            "Admin: `.env` mein `ASSISTANT_SESSION` add karo\n"
            "aur `setup_music()` properly call karo."
        )

    args = message.text.split(None, 1)
    if len(args) < 2:
        return await message.reply_text(
            "❌ **Kuch toh likho yaar!**\n\n"
            "Usage: `/play <song name ya URL>`\n\n"
            "**Examples:**\n"
            "`/play Tum Hi Ho Arijit Singh`\n"
            "`/play https://youtube.com/watch?v=...`\n"
            "`/play https://open.spotify.com/track/...`"
        )

    query = args[1].strip()
    requester = message.from_user.first_name if message.from_user else "Someone"
    msg = await message.reply_text("🔍 Dhoondh raha hoon...")

    # ── Spotify URL ──
    if re.search(r"open\.spotify\.com/(track|playlist)/", query):
        if not SPOTIFY_AVAILABLE:
            return await msg.edit_text(
                "❌ **Spotify support nahi hai!**\n\n"
                "`.env` mein add karo:\n"
                "```\nSPOTIFY_CLIENT_ID=xxx\nSPOTIFY_CLIENT_SECRET=xxx\n```\n"
                "Aur install karo: `pip install spotipy`"
            )
        result = await get_spotify_info(query)
        if isinstance(result, list):
            await msg.edit_text(f"📋 Spotify playlist se **{len(result)} songs** queue mein daal raha hoon...")
            added = 0
            for name in result:
                info = await search_youtube(name)
                if info:
                    info["requested_by"] = requester
                    music_queue[chat_id].append(info)
                    added += 1
            if added == 0:
                return await msg.edit_text("❌ Playlist ke koi bhi gaane nahi mile YouTube pe. Try again!")
            if not currently_playing.get(chat_id):
                await msg.edit_text(f"✅ {added} songs queue mein! Shuru karte hain... 🎵")
                await _play_next_internal(chat_id)
            else:
                await msg.edit_text(f"✅ **{added} songs queue mein add ho gaye!** 🎶")
            return
        elif isinstance(result, str):
            query = result
        else:
            return await msg.edit_text(
                "❌ **Spotify track nahi mila!**\n"
                "URL sahi hai? Ya seedha song naam likho."
            )

    # ── YouTube search / URL ──
    await msg.edit_text("🎵 YouTube pe dhoondh raha hoon...")
    track = await search_youtube(query)
    if not track:
        return await msg.edit_text(
            "❌ **Kuch nahi mila!**\n\n"
            "Dobara try karo, ya alag keywords use karo.\n"
            "Direct YouTube URL bhi de sakte ho."
        )

    track["requested_by"] = requester

    if currently_playing.get(chat_id):
        music_queue[chat_id].append(track)
        await msg.edit_text(
            f"✅ **Queue mein add ho gaya!**\n\n"
            f"🎵 **{track['title']}**\n"
            f"⏱ {_fmt_dur(track.get('duration', 0))}\n"
            f"📌 Position: #{len(music_queue[chat_id])}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 Queue Dekho", callback_data=f"mus:queue:{chat_id}")
            ]])
        )
    else:
        music_queue[chat_id].appendleft(track)
        await msg.edit_text(f"▶️ **{track['title']}** chal raha hai, ruko...")
        await _play_next_internal(chat_id)


async def pause_cmd(client: Client, message: Message):
    """/pause"""
    chat_id = message.chat.id
    if not calls or chat_id not in vc_joined:
        return await message.reply_text(
            "❌ **Abhi kuch play nahi ho raha!**\n"
            "Pehle `/play <song>` karo."
        )
    try:
        await calls.pause_stream(chat_id)
        await message.reply_text(
            "⏸ **Paused!**",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("▶️ Resume karo", callback_data=f"mus:resume:{chat_id}")
            ]])
        )
    except Exception as e:
        await message.reply_text(f"❌ **Pause karne mein dikkat aayi!**\nError: `{e}`")


async def resume_cmd(client: Client, message: Message):
    """/resume"""
    chat_id = message.chat.id
    if not calls or chat_id not in vc_joined:
        return await message.reply_text(
            "❌ **Kuch play nahi ho raha!**\n"
            "`/play <song>` se start karo."
        )
    try:
        await calls.resume_stream(chat_id)
        await message.reply_text("▶️ **Resumed! Enjoy karo~ 🎵**")
    except Exception as e:
        await message.reply_text(f"❌ **Resume nahi hua!**\nError: `{e}`")


async def skip_cmd(client: Client, message: Message):
    """/skip ya /next"""
    chat_id = message.chat.id
    if not currently_playing.get(chat_id):
        return await message.reply_text(
            "❌ **Skip karein toh sahi, kuch chal bhi raha ho!**\n"
            "Queue khaali hai. `/play` se add karo."
        )
    currently_playing[chat_id] = None
    if music_queue[chat_id]:
        await message.reply_text("⏭ **Skipping... agla song aa raha hai!**")
        await _play_next_internal(chat_id)
    else:
        await _do_stop(chat_id)
        await message.reply_text(
            "⏭ **Skip kiya — lekin queue mein koi song nahi tha!**\n"
            "VC se nikal gaya. `/play` se naya song lagao. 🎵"
        )


async def stop_cmd(client: Client, message: Message):
    """/stop"""
    chat_id = message.chat.id
    if not currently_playing.get(chat_id) and chat_id not in vc_joined:
        return await message.reply_text(
            "❌ **Kuch chal nahi raha toh band kya karein?** 😄\n"
            "`/play <song>` se shuru karo!"
        )
    await _do_stop(chat_id)
    await message.reply_text(
        "⏹ **Playback band kar diya!**\n"
        "Queue bhi clear ho gayi. VC se bhi nikal gaya. 👋"
    )


async def _do_stop(chat_id: int):
    """Internal stop helper."""
    music_queue[chat_id].clear()
    currently_playing[chat_id] = None
    loop_mode[chat_id] = "off"
    if calls and chat_id in vc_joined:
        try:
            await calls.leave_group_call(chat_id)
        except Exception:
            pass
        vc_joined.discard(chat_id)


async def queue_cmd(client: Client, message: Message):
    """/queue ya /q"""
    chat_id = message.chat.id
    queue = music_queue[chat_id]
    current = currently_playing.get(chat_id)

    if not current and not queue:
        return await message.reply_text(
            "📋 **Queue bilkul khaali hai!**\n"
            "`/play <song>` se koi gaana lagao. 🎶"
        )

    text = "📋 **Music Queue**\n\n"
    if current:
        text += (
            f"▶️ **Ab chal raha hai:**\n"
            f"`{current['title']}`\n"
            f"⏱ {_fmt_dur(current.get('duration', 0))} | 👤 {current['requested_by']}\n\n"
        )

    if queue:
        text += "**Aage ke songs:**\n"
        for i, t in enumerate(list(queue)[:10], 1):
            text += f"{i}. `{t['title']}` ({_fmt_dur(t.get('duration', 0))}) — {t['requested_by']}\n"
        if len(queue) > 10:
            text += f"\n_...aur {len(queue) - 10} songs queue mein hain._"
    else:
        text += "_Queue mein aur koi song nahi hai._"

    await message.reply_text(text, parse_mode=enums.ParseMode.MARKDOWN)


async def nowplaying_cmd(client: Client, message: Message):
    """/np ya /nowplaying"""
    chat_id = message.chat.id
    current = currently_playing.get(chat_id)
    if not current:
        return await message.reply_text(
            "❌ **Abhi kuch nahi chal raha!**\n"
            "`/play <song>` se shuru karo. 🎵"
        )
    lm = loop_mode.get(chat_id, "off")
    text = (
        f"🎵 **Now Playing**\n\n"
        f"**{current['title']}**\n"
        f"⏱ {_fmt_dur(current.get('duration', 0))}\n"
        f"👤 Requested by: {current['requested_by']}\n"
        f"🔁 Loop: {lm.upper()}\n"
        f"📋 Queue mein: {len(music_queue[chat_id])} songs"
    )
    await message.reply_text(text, reply_markup=_control_kb(chat_id), parse_mode=enums.ParseMode.MARKDOWN)


async def volume_cmd(client: Client, message: Message):
    """/volume <1-200>"""
    chat_id = message.chat.id
    if not calls or chat_id not in vc_joined:
        return await message.reply_text(
            "❌ **VC mein join nahi hoon!**\n"
            "Pehle `/play <song>` karo."
        )
    args = message.text.split()
    if len(args) < 2:
        return await message.reply_text(
            "❌ **Volume kitna karna hai batao!**\n"
            "Usage: `/volume 100`\n_(Range: 1 se 200 tak)_"
        )
    try:
        vol = int(args[1])
        if not 1 <= vol <= 200:
            raise ValueError
    except ValueError:
        return await message.reply_text(
            "❌ **Galat number!**\n"
            "1 se 200 ke beech koi number do.\n"
            "Example: `/volume 150`"
        )
    try:
        await calls.change_volume_call(chat_id, vol)
        emoji = "🔇" if vol < 20 else ("🔉" if vol < 80 else "🔊")
        await message.reply_text(f"{emoji} **Volume: {vol}%**")
    except Exception as e:
        await message.reply_text(f"❌ **Volume nahi bada!**\nError: `{e}`")


async def loop_cmd(client: Client, message: Message):
    """/loop [off|song|queue]"""
    chat_id = message.chat.id
    args = message.text.split()
    modes = ["off", "song", "queue"]

    if len(args) < 2 or args[1].lower() not in modes:
        current = loop_mode.get(chat_id, "off")
        return await message.reply_text(
            f"🔁 **Loop Mode**\n\n"
            f"Abhi: **{current.upper()}**\n\n"
            f"• `off` — loop band\n"
            f"• `song` — ek hi song repeat\n"
            f"• `queue` — saari queue repeat\n\n"
            f"Usage: `/loop off|song|queue`",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("➡️ Off",    callback_data=f"mus:lset:off:{chat_id}"),
                InlineKeyboardButton("🔁 Song",   callback_data=f"mus:lset:song:{chat_id}"),
                InlineKeyboardButton("🔄 Queue",  callback_data=f"mus:lset:queue:{chat_id}"),
            ]])
        )

    mode = args[1].lower()
    loop_mode[chat_id] = mode
    emojis = {"off": "➡️", "song": "🔁", "queue": "🔄"}
    await message.reply_text(f"{emojis[mode]} **Loop: {mode.upper()}**")


async def clearqueue_cmd(client: Client, message: Message):
    """/clearqueue"""
    chat_id = message.chat.id
    count = len(music_queue[chat_id])
    if count == 0:
        return await message.reply_text(
            "📋 **Queue pehle se hi khaali hai!** 😄\n"
            "Clear kya karna jo hai hi nahi."
        )
    music_queue[chat_id].clear()
    await message.reply_text(
        f"🗑 **Queue clear ho gayi!**\n"
        f"{count} songs hataaye gaye. Jo chal raha hai woh continue rahega."
    )


async def shuffle_cmd(client: Client, message: Message):
    """/shuffle"""
    chat_id = message.chat.id
    queue = music_queue[chat_id]
    if not queue:
        return await message.reply_text(
            "❌ **Queue mein koi song hi nahi hai shuffle karne ke liye!**\n"
            "Pehle `/play` se kuch add karo."
        )
    lst = list(queue)
    random.shuffle(lst)
    music_queue[chat_id] = deque(lst)
    await message.reply_text(
        f"🔀 **Queue shuffle ho gayi!**\n"
        f"{len(lst)} songs ki nayi order ready hai. 🎲"
    )


async def music_help_cmd(client: Client, message: Message):
    """/musichelp"""
    spotify_status = "✅ Ready" if SPOTIFY_AVAILABLE else "❌ Credentials chahiye"
    assistant_status = "✅ Connected" if (calls and assistant_client) else "❌ Setup karo"
    text = (
        "🎵 **Music Player — Help**\n\n"

        "**▶️ Playback:**\n"
        "• `/play <song ya URL>` — YouTube/Spotify se play karo\n"
        "• `/pause` — Pause karo\n"
        "• `/resume` — Resume karo\n"
        "• `/skip` ya `/next` — Agla song\n"
        "• `/stop` — Sab band, VC se niklo\n"
        "• `/np` — Ab kya chal raha hai\n\n"

        "**📋 Queue:**\n"
        "• `/queue` ya `/q` — Queue dekho\n"
        "• `/clearqueue` — Queue saaf karo\n"
        "• `/shuffle` — Queue random karo\n\n"

        "**⚙️ Settings:**\n"
        "• `/volume <1-200>` — Volume\n"
        "• `/loop [off|song|queue]` — Repeat mode\n\n"

        "**📡 Status:**\n"
        f"• Assistant: {assistant_status}\n"
        f"• YouTube: ✅ Ready\n"
        f"• Spotify: {spotify_status}\n\n"

        "_Buttons: Play ke baad automatically control panel aata hai!_"
    )
    await message.reply_text(text, parse_mode=enums.ParseMode.MARKDOWN)


# ─────────────────────────────────────────────────────────────────
#  Callback Handler
# ─────────────────────────────────────────────────────────────────

async def music_callback(client: Client, cq: CallbackQuery):
    data = cq.data  # e.g. "mus:pause:123456" or "mus:lset:song:123456"
    parts = data.split(":")

    # Parse action and chat_id
    if len(parts) == 3:
        _, action, chat_id_str = parts
        extra = None
    elif len(parts) == 4:
        _, action, extra, chat_id_str = parts
    else:
        return await cq.answer("❌ Invalid callback data", show_alert=True)

    try:
        chat_id = int(chat_id_str)
    except ValueError:
        return await cq.answer("❌ Invalid chat ID", show_alert=True)

    # Admin check
    try:
        member = await client.get_chat_member(chat_id, cq.from_user.id)
        is_admin = member.status in (
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER,
        )
    except Exception:
        is_admin = False

    if action not in ("queue",) and not is_admin:
        return await cq.answer("❌ Sirf admins yeh control kar sakte hain!", show_alert=True)

    await cq.answer()

    if action == "pause":
        if not calls or chat_id not in vc_joined:
            return await cq.answer("❌ Kuch play nahi ho raha!", show_alert=True)
        try:
            await calls.pause_stream(chat_id)
            await cq.edit_message_reply_markup(reply_markup=_control_kb(chat_id))
        except Exception as e:
            await cq.answer(f"Error: {e}", show_alert=True)

    elif action == "resume":
        if not calls or chat_id not in vc_joined:
            return await cq.answer("❌ Kuch play nahi ho raha!", show_alert=True)
        try:
            await calls.resume_stream(chat_id)
            await cq.answer("▶️ Resumed!")
        except Exception as e:
            await cq.answer(f"Error: {e}", show_alert=True)

    elif action == "skip":
        if not currently_playing.get(chat_id):
            return await cq.answer("❌ Kuch chal nahi raha!", show_alert=True)
        currently_playing[chat_id] = None
        if music_queue[chat_id]:
            await cq.edit_message_text("⏭ Skipping...")
            await _play_next_internal(chat_id)
        else:
            await _do_stop(chat_id)
            await cq.edit_message_text("⏹ Queue khatam! VC se nikal gaya.")

    elif action == "stop":
        await _do_stop(chat_id)
        await cq.edit_message_text("⏹ Playback band ho gaya. VC se nikal gaya.")

    elif action == "loop":
        modes = ["off", "song", "queue"]
        cur = loop_mode.get(chat_id, "off")
        loop_mode[chat_id] = modes[(modes.index(cur) + 1) % 3]
        try:
            await cq.edit_message_reply_markup(reply_markup=_control_kb(chat_id))
        except MessageNotModified:
            pass

    elif action == "lset":
        if extra not in ("off", "song", "queue"):
            return await cq.answer("❌ Invalid loop mode", show_alert=True)
        loop_mode[chat_id] = extra
        emojis = {"off": "➡️", "song": "🔁", "queue": "🔄"}
        await cq.edit_message_text(f"{emojis[extra]} **Loop: {extra.upper()}**",
                                    parse_mode=enums.ParseMode.MARKDOWN)

    elif action == "queue":
        q = music_queue[chat_id]
        cur = currently_playing.get(chat_id)
        if not cur and not q:
            return await cq.answer("Queue bilkul khaali hai!", show_alert=True)
        text = ""
        if cur:
            text += f"▶️ {cur['title'][:40]}\n\n"
        for i, t in enumerate(list(q)[:5], 1):
            text += f"{i}. {t['title'][:35]}\n"
        if len(q) > 5:
            text += f"...+{len(q)-5} more"
        await cq.answer(text[:200] or "Queue empty", show_alert=True)

    elif action == "vol_up":
        if not calls or chat_id not in vc_joined:
            return await cq.answer("❌ VC mein nahi hoon!", show_alert=True)
        try:
            await calls.change_volume_call(chat_id, 150)
            await cq.answer("🔊 Volume: 150%")
        except Exception as e:
            await cq.answer(f"Error: {e}", show_alert=True)

    elif action == "vol_down":
        if not calls or chat_id not in vc_joined:
            return await cq.answer("❌ VC mein nahi hoon!", show_alert=True)
        try:
            await calls.change_volume_call(chat_id, 50)
            await cq.answer("🔉 Volume: 50%")
        except Exception as e:
            await cq.answer(f"Error: {e}", show_alert=True)


# ─────────────────────────────────────────────────────────────────
#  Register Handlers
# ─────────────────────────────────────────────────────────────────

def register_music_handlers(app: Client):
    """
    Main bot file mein call karo:
        from music_module import register_music_handlers, setup_music
        calls = await setup_music(app, API_ID, API_HASH)
        if calls:
            register_music_handlers(app)
    """
    from pyrogram.handlers import CallbackQueryHandler, MessageHandler

    app.add_handler(MessageHandler(play_cmd,        filters.command("play")))
    app.add_handler(MessageHandler(pause_cmd,       filters.command("pause")))
    app.add_handler(MessageHandler(resume_cmd,      filters.command("resume")))
    app.add_handler(MessageHandler(skip_cmd,        filters.command(["skip", "next"])))
    app.add_handler(MessageHandler(stop_cmd,        filters.command("stop")))
    app.add_handler(MessageHandler(queue_cmd,       filters.command(["queue", "q"])))
    app.add_handler(MessageHandler(nowplaying_cmd,  filters.command(["np", "nowplaying"])))
    app.add_handler(MessageHandler(volume_cmd,      filters.command("volume")))
    app.add_handler(MessageHandler(loop_cmd,        filters.command("loop")))
    app.add_handler(MessageHandler(clearqueue_cmd,  filters.command("clearqueue")))
    app.add_handler(MessageHandler(shuffle_cmd,     filters.command("shuffle")))
    app.add_handler(MessageHandler(music_help_cmd,  filters.command("musichelp")))

    app.add_handler(CallbackQueryHandler(music_callback, filters.regex(r"^mus:")))

    print("✅ Music handlers registered!")


# ─────────────────────────────────────────────────────────────────
#  Help section (apne HELP_SECTIONS mein merge kar sakte ho)
# ─────────────────────────────────────────────────────────────────

MUSIC_HELP_SECTION = {
    "music": (
        "🎵 MUSIC PLAYER",
        (
            "Voice Chat mein music sunne ka full system!\n\n"
            "• `/play` — YouTube/Spotify se song lagao\n"
            "• `/pause`, `/resume`, `/skip`, `/stop`\n"
            "• `/np` — Ab kya chal raha hai\n"
            "• `/queue` — Queue dekho\n"
            "• `/shuffle` — Queue shuffle karo\n"
            "• `/volume` — Volume set karo\n"
            "• `/loop` — Repeat mode\n"
            "• `/musichelp` — Full help"
        )
    )
}
