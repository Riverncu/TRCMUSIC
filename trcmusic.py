```python
import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp
from collections import deque
import asyncio
import random
import datetime
import time
import logging
import base64
from pathlib import Path

from keep_alive import keep_alive

# Thiết lập logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

keep_alive()

# Dictionary for song queues and loop states
SONG_QUEUES = {}
LOOP_STATES = {}  # Store loop state per guild ("off", "song", "queue")
CURRENT_SONG = {}  # Store current song metadata per guild

async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts))

def _extract(query, ydl_opts):
    # Kiểm tra cookies
    cookies_path = Path(ydl_opts.get("cookiefile", "cookies.txt"))
    cookies_b64 = os.getenv('YTDLP_COOKIES')
    if cookies_b64:
        cookies_path.write_bytes(base64.b64decode(cookies_b64))
        ydl_opts['cookiefile'] = str(cookies_path)
    elif not cookies_path.exists():
        logging.error("Cookies file not found. Please provide valid cookies.txt or set YTDLP_COOKIES env var.")
        raise FileNotFoundError("Cookies file not found")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)

# ... (giữ nguyên toàn bộ code khác: intents, bot setup, queue, remove, shuffle, loop, nowplaying, skip, pause, resume, stop)

@bot.tree.command(name="play", description="Play a song or playlist or add it to the queue")
@app_commands.describe(query="Song name, YouTube URL, or playlist URL")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer(thinking=True)

    if interaction.user.voice is None:
        await interaction.followup.send(embed=discord.Embed(
            title="Error", 
            description="You must be in a voice channel to use this command.", 
            color=discord.Color.red()
        ))
        return

    voice_channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client
    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_channel != voice_client.channel:
        await voice_client.move_to(voice_channel)

    ydl_options = {
        "format": "bestaudio/best",
        "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
        "restrictfilenames": True,
        "noplaylist": False,
        "default_search": "ytsearch1",
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 15,
        "retries": 5,
        "extractor_retries": 5,
        "fragment_retries": 3,
        "sleep_interval": 1,
        "max_sleep_interval": 5,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Referer": "https://www.youtube.com/",
        },
        "force_ipv4": True,
        "no_check_certificate": True,
        "source_address": "0.0.0.0",
        "cookiefile": "cookies.txt",
        "extract_flat": "in_playlist",
        "verbose": True,
        "geo_bypass": True,
    }

    # Proxy từ Shadowrocket (không cần user/pass)
    proxy = os.getenv('YTDLP_PROXY')  # Ví dụ: socks5://45.67.89.12:1080
    if proxy:
        ydl_options['proxy'] = proxy
    else:
        logging.warning("No proxy set in YTDLP_PROXY env var. Using default connection.")

    try:
        start_time = time.time()
        results = await search_ytdlp_async(query, ydl_opts=ydl_options)
        logging.info(f"Search time for query '{query}': {time.time() - start_time:.2f}s")
        logging.debug(f"Raw yt_dlp results: {results}")
        tracks = results.get("entries", []) if results.get("entries") else [results]
    except Exception as e:
        logging.error(f"Failed to fetch song for query '{query}': {str(e)}")
        await interaction.followup.send(embed=discord.Embed(
            title="Error", 
            description=f"Failed to fetch song: {str(e)}. Check cookies or proxy.", 
            color=discord.Color.red()
        ))
        return

    if not tracks:
        await interaction.followup.send(embed=discord.Embed(
            title="Error", 
            description="No results found.", 
            color=discord.Color.red()
        ))
        return

    guild_id = str(interaction.guild_id)
    if guild_id not in SONG_QUEUES:
        SONG_QUEUES[guild_id] = deque()

    added_songs = []
    for track in tracks:
        audio_url = track.get("url") or track.get("webpage_url") or track.get("id")  # Fallback URL
        if not audio_url:
            logging.warning(f"No valid URL for track: {track.get('title', 'Unknown')}")
            continue
        title = track.get("title", "Untitled")
        duration = track.get("duration", 0)
        SONG_QUEUES[guild_id].append((audio_url, title, duration, interaction.user.name))
        added_songs.append(title)
    logging.info(f"Added songs to queue for guild {guild_id}: {added_songs}")

    if not added_songs:
        await interaction.followup.send(embed=discord.Embed(
            title="Error", 
            description="No valid songs could be added to the queue. Check query or cookies.", 
            color=discord.Color.red()
        ))
        return

    if len(added_songs) == 1:
        message = f"Added to queue: **{added_songs[0]}**"
    else:
        message = f"Added {len(added_songs)} songs to queue from playlist."

    if voice_client.is_playing() or voice_client.is_paused():
        await interaction.followup.send(embed=discord.Embed(
            title="Added", 
            description=message, 
            color=discord.Color.green()
        ))
    else:
        await interaction.followup.send(embed=discord.Embed(
            title="Playing", 
            description=f"Now playing: **{added_songs[0]}**", 
            color=discord.Color.green()
        ))
        await play_next_song(voice_client, guild_id, interaction.channel)

# ... (giữ nguyên toàn bộ code play_next_song và các phần khác như gốc)

# Run the bot
bot.run(TOKEN)
```
