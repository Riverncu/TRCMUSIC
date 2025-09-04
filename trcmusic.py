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
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)

# Setup intents
intents = discord.Intents.default()
intents.message_content = True

# Bot setup
bot = commands.Bot(command_prefix="r", intents=intents)

@bot.event
async def on_ready():
    SONG_QUEUES.clear()  # Xóa queue cũ
    LOOP_STATES.clear()  # Xóa trạng thái loop cũ
    CURRENT_SONG.clear()  # Xóa thông tin bài hát cũ
    guild = discord.Object(id=1097785025602261043)  # Đồng bộ cho server cụ thể
    await bot.tree.sync(guild=guild)
    logging.info(f"{bot.user} is online!")

@bot.event
async def on_disconnect():
    """Dọn dẹp process khi bot disconnect"""
    logging.info("Cleaning up FFmpeg processes...")
    
    # Dọn dẹp các biến global để tránh memory leak
    SONG_QUEUES.clear()
    LOOP_STATES.clear()
    CURRENT_SONG.clear()
    
    # Thêm cleanup process nếu cần
    for guild_id in list(SONG_QUEUES.keys()):
        try:
            # Disconnect khỏi tất cả voice channels
            for guild in bot.guilds:
                voice_client = guild.voice_client
                if voice_client and voice_client.is_connected():
                    await voice_client.disconnect()
        except Exception as e:
            logging.error(f"Error cleaning up guild {guild_id}: {e}")

@bot.event
async def on_error(event, *args, **kwargs):
    """Xử lý lỗi tổng thể - chỉ log lại, không crash bot"""
    logging.error(f"Error in event {event}: {args} {kwargs}")
    
    # Xử lý các lỗi cụ thể
    if "403" in str(args) or "403" in str(kwargs):
        logging.warning("YouTube 403 error detected - ignoring")
        return  # Bỏ qua lỗi 403

@bot.tree.command(name="queue", description="Show the current song queue")
async def queue(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    queue = SONG_QUEUES.get(guild_id, deque())
    logging.info(f"Queue for guild {guild_id}: {queue}")
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

    # Tính thanh tiến độ
    current_time = time.time()
    start_time = song_info.get("start_time", current_time)
    duration = song_info.get("duration", 0)
    if duration > 0:
        progress = (current_time - start_time) / duration
        progress = min(max(progress, 0), 1)  # Giới hạn 0-1
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


import yt_dlp
import asyncio
import logging
from collections import deque

@bot.tree.command(name="play", description="Play a song or playlist or add it to the queue")
@app_commands.describe(query="Song name, YouTube URL, or playlist URL")
async def play(interaction: discord.Interaction, query: str):
    # Phản hồi ngay để tránh timeout
    await interaction.response.defer(thinking=True)
    
    # Gửi thông báo "Đang tìm kiếm"
    processing_msg = await interaction.followup.send(embed=discord.Embed(
        title="🔍 Searching...",
        description="Please wait while I search for your song",
        color=discord.Color.blue()
    ))

    # Kiểm tra voice channel
    if not interaction.user.voice or not interaction.user.voice.channel:
        await processing_msg.edit(embed=discord.Embed(
            title="Error", 
            description="You must be in a voice channel to use this command.", 
            color=discord.Color.red()
        ))
        return

    voice_channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client
    
    try:
        if voice_client is None:
            voice_client = await voice_channel.connect()
        elif voice_channel != voice_client.channel:
            await voice_client.move_to(voice_channel)
    except Exception as e:
        await processing_msg.edit(embed=discord.Embed(
            title="Error", 
            description=f"Failed to connect to voice channel: {str(e)}", 
            color=discord.Color.red()
        ))
        return

    # Tối ưu ydl_options
    ydl_options = {
        "format": "bestaudio[abr<=96]/bestaudio",
        "extract_flat": False,  # Tắt extract_flat để lấy đầy đủ URL
        "noplaylist": False,
        "default_search": "ytsearch1",
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 2,
        "retries": 2,
        "source_address": "0.0.0.0",
        "cookiefile": "cookies.txt",
        "extractor_args": {
            "youtube": {
                "skip": ["dash", "hls", "thumbnails"],
                "player_client": ["android"],
                "lang": "en",
                "max_results": 1,
            }
        },
        "force_ipv4": True,
        "no_check_certificate": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Android 13; Mobile; rv:68.0) Gecko/68.0 Firefox/120.0",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.youtube.com/",
        },
        "max_downloads": 3,
        "playlistend": 5,
        "cachedir": False,
    }

    # Search với timeout
    try:
        start_time = time.time()
        with yt_dlp.YoutubeDL(ydl_options) as ydl:
            results = await bot.loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
        logging.info(f"Search time for query '{query}': {time.time() - start_time:.2f}s")
    except asyncio.TimeoutError:
        await processing_msg.edit(embed=discord.Embed(
            title="❌ Timeout",
            description="Search took too long. Try a different query or check your connection.",
            color=discord.Color.red()
        ))
        return
    except Exception as e:
        logging.error(f"Failed to fetch song for query '{query}': {str(e)}")
        await processing_msg.edit(embed=discord.Embed(
            title="Error", 
            description=f"Failed to fetch song: {str(e)}", 
            color=discord.Color.red()
        ))
        return

    # Xử lý kết quả
    tracks = results.get("entries", []) if results.get("entries") else [results]
    
    if not tracks:
        await processing_msg.edit(embed=discord.Embed(
            title="Error", 
            description="No results found.", 
            color=discord.Color.red()
        ))
        return

    guild_id = str(interaction.guild_id)
    if guild_id not in SONG_QUEUES:
        SONG_QUEUES[guild_id] = deque()

    added_songs = []
    BATCH_SIZE = 3
    
    for track in tracks:
        if not track:
            continue
            
        audio_url = track.get("url", "")
        title = track.get("title", "Untitled")
        duration = track.get("duration", 0)
        
        if not audio_url:
            logging.warning(f"No valid URL for track {title}")
            continue
            
        SONG_QUEUES[guild_id].append((audio_url, title, duration, interaction.user.name))
        added_songs.append(title)
        
        # Update progress định kỳ
        if len(added_songs) % BATCH_SIZE == 0 and len(added_songs) < 5:
            try:
                await processing_msg.edit(embed=discord.Embed(
                    title="📥 Processing...",
                    description=f"Added {len(added_songs)} songs so far",
                    color=discord.Color.blue()
                ))
            except:
                pass

    if not added_songs:
        await processing_msg.edit(embed=discord.Embed(
            title="Error", 
            description="No valid songs could be added.", 
            color=discord.Color.red()
        ))
        return

    # Gửi kết quả cuối
    if len(added_songs) == 1:
        message = f"✅ Added to queue: **{added_songs[0]}**"
    else:
        message = f"✅ Added {len(added_songs)} songs to queue."

    if voice_client.is_playing() or voice_client.is_paused():
        await processing_msg.edit(embed=discord.Embed(
            title="Success", 
            description=message, 
            color=discord.Color.green()
        ))
    else:
        await processing_msg.edit(embed=discord.Embed(
            title="Now Playing", 
            description=f"🎵 Now playing: **{added_songs[0]}**", 
            color=discord.Color.green()
        ))
        asyncio.create_task(play_next_song(voice_client, guild_id, interaction.channel))


async def play_next_song(voice_client, guild_id, channel):
    if not voice_client.is_connected():
        logging.warning(f"Voice client not connected for guild {guild_id}")
        await channel.send(embed=discord.Embed(
            title="Error",
            description="Bot is not connected to voice channel.",
            color=discord.Color.red()
        ))
        return

    if not SONG_QUEUES[guild_id]:
        await voice_client.disconnect()
        SONG_QUEUES[guild_id] = deque()
        LOOP_STATES[guild_id] = "off"
        logging.info(f"Queue empty, disconnected from voice for guild {guild_id}")
        return

    try:
        audio_url, title, duration, requester = SONG_QUEUES[guild_id].popleft()
        index = len(SONG_QUEUES[guild_id]) + 1
    except ValueError as e:
        logging.error(f"Error unpacking queue item in guild {guild_id}: {e}")
        await channel.send(embed=discord.Embed(
            title="Error",
            description="Invalid queue item detected. Clearing queue.",
            color=discord.Color.red()
        ))
        SONG_QUEUES[guild_id].clear()
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
        SONG_QUEUES[guild_id].append((audio_url, title, duration, requester))
    elif loop_mode == "queue" and not SONG_QUEUES[guild_id]:
        SONG_QUEUES[guild_id].append((audio_url, title, duration, requester))

    ffmpeg_options = {
        "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin",
        "options": "-vn -c:a libopus -b:a 96k",
    }

    try:
        source = discord.FFmpegOpusAudio(audio_url, **ffmpeg_options, executable="/usr/bin/ffmpeg")
    except Exception as e:
        logging.error(f"FFmpeg failed to create source for {title} (URL: {audio_url}): {str(e)}")
        await channel.send(embed=discord.Embed(
            title="Error",
            description=f"Failed to play {title}: Invalid or inaccessible URL.",
            color=discord.Color.red()
        ))
        asyncio.create_task(play_next_song(voice_client, guild_id, channel))
        return

    def after_play(error):
        if error:
            logging.error(f"Playback error for {title} (URL: {audio_url}): {str(error)}")
            asyncio.create_task(channel.send(embed=discord.Embed(
                title="Error",
                description=f"Playback failed for {title}: {str(error)}",
                color=discord.Color.red()
            )))
        else:
            logging.info(f"Finished playing {title} for guild {guild_id}")
        asyncio.create_task(play_next_song(voice_client, guild_id, channel))

    try:
        voice_client.play(source, after=after_play)
        await channel.send(embed=discord.Embed(
            title="Now Playing", 
            description=f"**{title}** (Index: {index}, Requested by {requester})", 
            color=discord.Color.blue()
        ))
    except Exception as e:
        logging.error(f"Voice client error for {title} (URL: {audio_url}): {str(e)}")
        await channel.send(embed=discord.Embed(
            title="Error",
            description="Failed to play audio due to voice connection issue.",
            color=discord.Color.red()
        ))
        asyncio.create_task(play_next_song(voice_client, guild_id, channel))

# Run the bot

bot.run(TOKEN)










