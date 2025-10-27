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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

keep_alive()

SONG_QUEUES = {}
LOOP_STATES = {}
CURRENT_SONG = {}

# Define ydl_options globally
ydl_options = {
    "format": "bestaudio/best",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "default_search": "ytsearch1",
    "quiet": True,
    "no_warnings": True,
    "socket_timeout": 8,
    "retries": 2,
    "extractor_retries": 2,
    "fragment_retries": 2,
    "sleep_interval": 0.5,
    "max_sleep_interval": 2,
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Referer": "https://www.youtube.com/",
    },
    "force_ipv4": True,
    "source_address": "0.0.0.0",
    "cookiefile": "cookies.txt",
    "extract_flat": "in_playlist",
    "cachedir": "/tmp/yt_dlp_cache",
    "force_generic_extractor": True,
    "geo_bypass": True,
    "nocheckcertificate": True,
    # (tuỳ chọn) giúp yt-dlp đổi client khi cần
    # "extractor_args": {"youtube": {"player_client": ["web","android","ios"]}},
}

ffmpeg_options = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin",
    "options": "-vn -c:a libopus -b:a 96k -bufsize 64k -frame_duration 20 -application lowdelay",
}

# === NEW: helper gắn cookies vào ydl options (dùng chung cho search & resolve) ===
def prepare_ydl_opts(base_opts: dict) -> dict:
    opts = dict(base_opts)  # copy để không đụng bản gốc
    cookies_path = Path(opts.get("cookiefile", "cookies.txt"))
    cookies_b64 = os.getenv("YTDLP_COOKIES")

    if cookies_b64:
        try:
            cookies_path.write_bytes(base64.b64decode(cookies_b64))
            opts["cookiefile"] = str(cookies_path)
            logging.info(f"Loaded cookies from env to {cookies_path}")
        except Exception as e:
            logging.warning(f"Failed to write cookies from env: {e}")
    elif cookies_path.exists():
        opts["cookiefile"] = str(cookies_path)
        logging.info(f"Using existing cookies file at {cookies_path}")
    else:
        logging.warning("Cookies file not found and YTDLP_COOKIES is empty. YouTube may require login.")

    return opts
# === END NEW ===

async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts))

def _extract(query, ydl_opts):
    # === CHANGED: dùng helper cookies chung ===
    opts = prepare_ydl_opts(ydl_opts)
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            return ydl.extract_info(query, download=False)
        except Exception as e:
            logging.error(f"yt-dlp extraction failed: {e}")
            raise

# === NEW: helper resolve URL stream trực tiếp cho 1 entry (kể cả kênh/playlist/flat) ===
async def resolve_stream_url_async(entry):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _resolve_stream_url(entry))

def _resolve_stream_url(entry):
    """
    Trả về tuple (stream_url, title, duration) đã được yt_dlp resolve thành URL audio trực tiếp.
    Nếu entry là kênh/playlist/flat entry -> re-extract bằng extract_flat=False để lấy formats thật.
    """
    target = entry.get("webpage_url") or entry.get("url")
    if not target:
        return None

    local_opts = dict(ydl_options)
    local_opts.update({
        "extract_flat": False,            # cần full info + formats
        "noplaylist": True,               # chỉ lấy 1 video
        "quiet": True,
        "force_generic_extractor": False, # dùng extractor gốc YouTube
    })
    # === CHANGED: gắn cookies trước khi gọi yt-dlp ===
    local_opts = prepare_ydl_opts(local_opts)

    try:
        with yt_dlp.YoutubeDL(local_opts) as ydl:
            info = ydl.extract_info(target, download=False)
    except Exception as e:
        logging.error(f"Resolve stream failed for target={target}: {e}")
        return None

    # Nếu là list/playlist/channel -> chọn phần tử đầu là video và re-extract nếu cần
    if isinstance(info, dict) and "entries" in info and info["entries"]:
        for e in info["entries"]:
            if not e:
                continue
            sub_target = e.get("webpage_url") or e.get("url")
            if not sub_target:
                continue
            try:
                with yt_dlp.YoutubeDL(local_opts) as ydl2:
                    e2 = ydl2.extract_info(sub_target, download=False)
                    if e2 and e2.get("url"):
                        return (e2["url"], e2.get("title", "Untitled"), e2.get("duration", 0))
            except Exception as ex2:
                logging.warning(f"Second-stage resolve failed: {ex2}")
                continue
        return None

    # Trường hợp single video đã có stream URL
    if info and info.get("url"):
        return (info["url"], info.get("title", "Untitled"), info.get("duration", 0))

    return None
# === END NEW ===

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="r", intents=intents)

@bot.event
async def on_ready():
    SONG_QUEUES.clear()
    LOOP_STATES.clear()
    CURRENT_SONG.clear()
    await bot.tree.sync()
    logging.info(f"{bot.user} is online!")

@bot.tree.command(name="queue", description="Show the current song queue")
async def queue(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    queue = SONG_QUEUES.get(guild_id, deque())
    
    if not queue:
        await interaction.response.send_message(embed=discord.Embed(
            title="Queue", 
            description="The queue is empty!", 
            color=discord.Color.red()
        ))
        return

    embed = discord.Embed(title="Song Queue", color=discord.Color.blue())
    for i, (_, title, duration, requester) in enumerate(queue, 1):
        duration_str = str(datetime.timedelta(seconds=int(duration)))
        embed.add_field(
            name=f"{i}. {title}",
            value=f"Duration: {duration_str} | Requested by: {requester}",
            inline=False
        )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="remove", description="Remove a song from the queue by position")
@app_commands.describe(position="Position of the song in the queue")
async def remove(interaction: discord.Interaction, position: int):
    guild_id = str(interaction.guild_id)
    queue = SONG_QUEUES.get(guild_id, deque())
    
    if position < 1 or position > len(queue):
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", 
            description="Invalid position!", 
            color=discord.Color.red()
        ))
        return

    song = queue[position - 1]
    queue.remove(song)
    await interaction.response.send_message(embed=discord.Embed(
        title="Removed", 
        description=f"Removed: **{song[1]}** from position {position}", 
        color=discord.Color.green()
    ))

@bot.tree.command(name="shuffle", description="Shuffle the current queue")
async def shuffle(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    queue = SONG_QUEUES.get(guild_id, deque())
    
    if not queue:
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", 
            description="The queue is empty!", 
            color=discord.Color.red()
        ))
        return

    queue_list = list(queue)
    random.shuffle(queue_list)
    SONG_QUEUES[guild_id] = deque(queue_list)
    await interaction.response.send_message(embed=discord.Embed(
        title="Shuffled", 
        description="The queue has been shuffled!", 
        color=discord.Color.green()
    ))

@bot.tree.command(name="loop", description="Set loop mode: off, song, or queue")
@app_commands.describe(mode="Loop mode: off, song, or queue")
async def loop(interaction: discord.Interaction, mode: str):
    guild_id = str(interaction.guild_id)
    mode = mode.lower()
    
    if mode not in ["off", "song", "queue"]:
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", 
            description="Invalid mode! Use 'off', 'song', or 'queue'.", 
            color=discord.Color.red()
        ))
        return

    LOOP_STATES[guild_id] = mode
    await interaction.response.send_message(embed=discord.Embed(
        title="Loop Mode", 
        description=f"Loop mode set to: **{mode}**", 
        color=discord.Color.green()
    ))

@bot.tree.command(name="nowplaying", description="Show details of the current song")
async def nowplaying(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    voice_client = interaction.guild.voice_client
    
    if not voice_client or not voice_client.is_playing():
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", 
            description="No song is currently playing!", 
            color=discord.Color.red()
        ))
        return

    song_info = CURRENT_SONG.get(guild_id, {})
    if not song_info:
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", 
            description="No song information available!", 
            color=discord.Color.red()
        ))
        return

    current_time = time.time()
    start_time = song_info.get("start_time", current_time)
    duration = song_info.get("duration", 0)
    if duration > 0:
        progress = (current_time - start_time) / duration
        progress = min(max(progress, 0), 1)
        bar_length = 20
        filled = int(bar_length * progress)
        bar = "█" * filled + "▒" * (bar_length - filled)
        progress_str = f"{bar} {int(progress * 100)}%"
    else:
        progress_str = "N/A"

    embed = discord.Embed(title="Now Playing", color=discord.Color.blue())
    embed.add_field(name="Index", value=song_info.get("index", "N/A"), inline=True)
    embed.add_field(name="Title", value=song_info.get("title", "Unknown"), inline=False)
    embed.add_field(name="Duration", value=str(datetime.timedelta(seconds=int(song_info.get("duration", 0)))), inline=True)
    embed.add_field(name="Requested by", value=song_info.get("requester", "Unknown"), inline=True)
    embed.add_field(name="Progress", value=progress_str, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="skip", description="Skips the current playing song")
async def skip(interaction: discord.Interaction):
    if interaction.guild.voice_client and (interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused()):
        interaction.guild.voice_client.stop()
        await interaction.response.send_message(embed=discord.Embed(
            title="Skipped", 
            description="Skipped the current song.", 
            color=discord.Color.green()
        ))
    else:
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", 
            description="Not playing anything to skip.", 
            color=discord.Color.red()
        ))

@bot.tree.command(name="pause", description="Pause the currently playing song")
async def pause(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if voice_client is None:
        return await interaction.response.send_message(embed=discord.Embed(
            title="Error", 
            description="I'm not in a voice channel.", 
            color=discord.Color.red()
        ))

    if not voice_client.is_playing():
        return await interaction.response.send_message(embed=discord.Embed(
            title="Error", 
            description="Nothing is currently playing.", 
            color=discord.Color.red()
        ))
    
    voice_client.pause()
    await interaction.response.send_message(embed=discord.Embed(
        title="Paused", 
        description="Playback paused!", 
        color=discord.Color.green()
    ))

@bot.tree.command(name="resume", description="Resume the currently paused song")
async def resume(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if voice_client is None:
        return await interaction.response.send_message(embed=discord.Embed(
            title="Error", 
            description="I'm not in a voice channel.", 
            color=discord.Color.red()
        ))

    if not voice_client.is_paused():
        return await interaction.response.send_message(embed=discord.Embed(
            title="Error", 
            description="I'm not paused right now.", 
            color=discord.Color.red()
        ))
    
    voice_client.resume()
    await interaction.response.send_message(embed=discord.Embed(
        title="Resumed", 
        description="Playback resumed!", 
        color=discord.Color.green()
    ))

@bot.tree.command(name="stop", description="Stop playback and clear the queue")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_connected():
        return await interaction.response.send_message(embed=discord.Embed(
            title="Error", 
            description="I'm not connected to any voice channel.", 
            color=discord.Color.red()
        ))

    guild_id = str(interaction.guild_id)
    if guild_id in SONG_QUEUES:
        SONG_QUEUES[guild_id].clear()
    if guild_id in LOOP_STATES:
        LOOP_STATES[guild_id] = "off"

    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()

    await voice_client.disconnect()
    await interaction.response.send_message(embed=discord.Embed(
        title="Stopped", 
        description="Stopped playback and disconnected!", 
        color=discord.Color.green()
    ))

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

    try:
        start_time = time.time()
        results = await search_ytdlp_async(query, ydl_opts=ydl_options)
        logging.info(f"Search time for query '{query}': {time.time() - start_time:.2f}s")
        
        # Handle both single videos and playlists
        if 'entries' in results:
            tracks = [entry for entry in results['entries'] if entry is not None]
        else:
            tracks = [results]
            
    except Exception as e:
        logging.error(f"Failed to fetch song for query '{query}': {str(e)}")
        await interaction.followup.send(embed=discord.Embed(
            title="Error", 
            description=f"Failed to fetch song: {str(e)}", 
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
        if not track:
            continue

        # === CHANGED: luôn resolve thành stream URL trực tiếp trước khi enqueue (có cookies) ===
        resolved = await resolve_stream_url_async(track)
        if not resolved:
            logging.warning(f"Cannot resolve playable stream for: {track.get('title', 'Unknown')}")
            continue

        audio_url, fixed_title, fixed_duration = resolved
        title = fixed_title or track.get("title", "Untitled")
        duration = fixed_duration if fixed_duration is not None else track.get("duration", 0)

        SONG_QUEUES[guild_id].append((audio_url, title, duration, interaction.user.name))
        added_songs.append(title)
    # === END CHANGED ===
        
    logging.info(f"Added songs to queue for guild {guild_id}: {added_songs}")

    if not added_songs:
        await interaction.followup.send(embed=discord.Embed(
            title="Error", 
            description="No valid songs could be added to the queue.", 
            color=discord.Color.red()
        ))
        return

    if len(added_songs) == 1:
        message = f"Added to queue: **{added_songs[0]}**"
    else:
        message = f"Added {len(added_songs)} songs to queue."

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

async def play_next_song(voice_client, guild_id, channel):
    if not voice_client or not voice_client.is_connected():
        logging.warning(f"Voice client not connected for guild {guild_id}")
        return

    if guild_id not in SONG_QUEUES or not SONG_QUEUES[guild_id]:
        if voice_client.is_connected():
            await voice_client.disconnect()
        logging.info(f"Queue empty, disconnected from voice for guild {guild_id}")
        return

    try:
        song = SONG_QUEUES[guild_id].popleft()
        audio_url, title, duration, requester = song
        index = len(SONG_QUEUES[guild_id]) + 1
    except (ValueError, IndexError) as e:
        logging.error(f"Error unpacking queue item in guild {guild_id}: {e}")
        SONG_QUEUES[guild_id].clear()
        if voice_client.is_connected():
            await voice_client.disconnect()
        return

    CURRENT_SONG[guild_id] = {
        "title": title,
        "duration": duration,
        "requester": requester,
        "url": audio_url,
        "index": index,
        "start_time": time.time()
    }
    logging.info(f"Attempting to play: {title} (URL: {audio_url}) for guild {guild_id}")

    loop_mode = LOOP_STATES.get(guild_id, "off")
    if loop_mode == "song":
        SONG_QUEUES[guild_id].appendleft((audio_url, title, duration, requester))
    elif loop_mode == "queue":
        SONG_QUEUES[guild_id].append((audio_url, title, duration, requester))

    try:
        source = discord.FFmpegPCMAudio(audio_url, **ffmpeg_options)
    except Exception as e:
        logging.error(f"FFmpeg failed to create source for {title}: {str(e)}")
        await channel.send(embed=discord.Embed(
            title="Error",
            description=f"Failed to play {title}: {str(e)}",
            color=discord.Color.red()
        ))
        await play_next_song(voice_client, guild_id, channel)
        return

    def after_play(error):
        if error:
            logging.error(f"Playback error for {title}: {str(error)}")
        else:
            logging.info(f"Finished playing {title} for guild {guild_id}")
        
        # Schedule the next song in the event loop
        coro = play_next_song(voice_client, guild_id, channel)
        asyncio.run_coroutine_threadsafe(coro, bot.loop)

    try:
        voice_client.play(source, after=after_play)
        # Send now playing message
        asyncio.run_coroutine_threadsafe(
            channel.send(embed=discord.Embed(
                title="Now Playing", 
                description=f"**{title}** (Requested by {requester})", 
                color=discord.Color.blue()
            )),
            bot.loop
        )
    except Exception as e:
        logging.error(f"Voice client error for {title}: {str(e)}")
        await play_next_song(voice_client, guild_id, channel)

bot.run(TOKEN)
