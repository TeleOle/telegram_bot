# save as bot_add_channel.py
# Requires: python-telegram-bot>=20.0
# pip install python-telegram-bot --upgrade

import logging
import os
import json
import random
import subprocess
import uuid
import asyncio
from pathlib import Path
from typing import Dict, Any
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Chat,
    MessageOriginChannel,
    ReactionTypeEmoji,
    MessageEntity,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaAnimation,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError

# --- Configure logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration ---
class Config:
    """
    Configuration loaded from environment variables.
    Set these environment variables before running:
    - TELEGRAM_API_ID
    - TELEGRAM_API_HASH
    - MANAGER_BOT_TOKEN
    - MAIN_ADMIN_ID
    """
    API_ID = int(os.getenv('TELEGRAM_API_ID', '0'))
    API_HASH = os.getenv('TELEGRAM_API_HASH', '')
    BOT_TOKEN = os.getenv('MANAGER_BOT_TOKEN', '')
    MAIN_ADMIN_ID = int(os.getenv('MAIN_ADMIN_ID', '0'))

    # Data file configuration
    DATA_FILE = Path("bot_data.json")

    @classmethod
    def validate(cls):
        """Validate that all required configuration is present."""
        if not all([cls.API_ID, cls.API_HASH, cls.BOT_TOKEN, cls.MAIN_ADMIN_ID]):
            raise ValueError("Missing required configuration values!")
        logger.info("✓ Configuration loaded successfully")
        logger.info(f"  - API_ID: {cls.API_ID}")
        logger.info(f"  - Bot Token: {cls.BOT_TOKEN[:20]}...")
        logger.info(f"  - Admin ID: {cls.MAIN_ADMIN_ID}")

# --- Temporary directory for media files ---
TEMP_DIR = Path("temp_media")
TEMP_DIR.mkdir(exist_ok=True)

# --- Simple in-memory storage: user_id -> list of channels (dicts) ---
USER_CHANNELS: Dict[int, list[Dict[str, Any]]] = {}

# --- Data Persistence Functions ---
def load_data():
    """Load user data from JSON file."""
    global USER_CHANNELS
    if Config.DATA_FILE.exists():
        try:
            with open(Config.DATA_FILE, 'r') as f:
                data = json.load(f)
                # Convert string keys back to integers
                USER_CHANNELS = {int(k): v for k, v in data.items()}
            logger.info(f"✓ Loaded data for {len(USER_CHANNELS)} users")
        except Exception as e:
            logger.error(f"✗ Error loading data: {e}")
            USER_CHANNELS = {}
    else:
        logger.info("No data file found, starting fresh")
        USER_CHANNELS = {}

def save_data():
    """Save user data to JSON file."""
    try:
        with open(Config.DATA_FILE, 'w') as f:
            json.dump(USER_CHANNELS, f, indent=2)
        logger.debug("Data saved successfully")
    except Exception as e:
        logger.error(f"✗ Error saving data: {e}")

# --- Helper Functions for Media Processing ---
async def download_telegram_file(context: ContextTypes.DEFAULT_TYPE, file_id: str, file_extension: str, max_size_mb: int = 20) -> Path:
    """Downloads a file from Telegram and returns its local path.

    Args:
        context: Bot context
        file_id: Telegram file ID
        file_extension: File extension
        max_size_mb: Maximum file size in MB (default 20MB - Telegram bot API limit)

    Raises:
        Exception: If file is too large
    """
    new_file = await context.bot.get_file(file_id)

    # Check file size (file_size is in bytes)
    file_size_mb = new_file.file_size / (1024 * 1024)
    logger.info(f"File size: {file_size_mb:.2f} MB")

    if file_size_mb > max_size_mb:
        raise Exception(f"File is too large ({file_size_mb:.2f} MB). Maximum size is {max_size_mb} MB.")

    unique_filename = f"{uuid.uuid4()}.{file_extension}"
    download_path = TEMP_DIR / unique_filename
    await new_file.download_to_drive(download_path)
    logger.info(f"Downloaded file to {download_path}")
    return download_path

async def apply_image_watermark(
    input_path: Path,
    output_path: Path,
    watermark_image_path: Path,
    position: str,
    size: int,
    transparency: int,
    quality: int,
    is_video: bool,
    rotation: int = 0,
    effect: str = "none",
    effect_speed: int = 50,
) -> Path:
    """Apply image/GIF watermark overlay using FFmpeg with rotation and moving effects."""
    try:
        # Calculate watermark size as percentage of main media
        watermark_scale = f"iw*{size/100}"

        # Calculate alpha (transparency)
        alpha = (100 - transparency) / 100

        # Position mapping for overlay
        positions = {
            "top_left": "10:10",
            "top_center": "(main_w-overlay_w)/2:10",
            "top_right": "main_w-overlay_w-10:10",
            "mid_left": "10:(main_h-overlay_h)/2",
            "center": "(main_w-overlay_w)/2:(main_h-overlay_h)/2",
            "mid_right": "main_w-overlay_w-10:(main_h-overlay_h)/2",
            "bottom_left": "10:main_h-overlay_h-10",
            "bottom_center": "(main_w-overlay_w)/2:main_h-overlay_h-10",
            "bottom_right": "main_w-overlay_w-10:main_h-overlay_h-10",
        }

        overlay_position = positions.get(position, "main_w-overlay_w-10:main_h-overlay_h-10")

        # Build watermark filter with rotation
        watermark_filter = f"[1:v]scale={watermark_scale}:-1,format=rgba"

        # Add rotation if specified (rotation works for images/GIFs!)
        if rotation != 0:
            # Convert degrees to radians for FFmpeg
            radians = rotation * 3.14159 / 180
            watermark_filter += f",rotate={radians}:c=none:ow='hypot(iw,ih)':oh=ow"

        # Add transparency
        watermark_filter += f",colorchannelmixer=aa={alpha}[wm]"

        # Build overlay filter with moving effects for videos
        if is_video and effect in ["move_diagonal_dr", "move_diagonal_dl", "move_diagonal_ur", "move_diagonal_ul"]:
            # Moving diagonal effects
            speed_factor = effect_speed / 50.0  # 1-100 -> 0.02-2.0

            if effect == "move_diagonal_dr":
                # Top-Left → Down-Right
                x_expr = f"t*{speed_factor*100}*W/10"
                y_expr = f"t*{speed_factor*100}*H/10"
                overlay_filter = f"[0:v][wm]overlay={x_expr}:{y_expr}:shortest=1"

            elif effect == "move_diagonal_dl":
                # Top-Right → Down-Left  
                x_expr = f"W-overlay_w-t*{speed_factor*100}*W/10"
                y_expr = f"t*{speed_factor*100}*H/10"
                overlay_filter = f"[0:v][wm]overlay={x_expr}:{y_expr}:shortest=1"

            elif effect == "move_diagonal_ur":
                # Bottom-Left → Up-Right
                x_expr = f"t*{speed_factor*100}*W/10"
                y_expr = f"H-overlay_h-t*{speed_factor*100}*H/10"
                overlay_filter = f"[0:v][wm]overlay={x_expr}:{y_expr}:shortest=1"

            elif effect == "move_diagonal_ul":
                # Bottom-Right → Up-Left
                x_expr = f"W-overlay_w-t*{speed_factor*100}*W/10"
                y_expr = f"H-overlay_h-t*{speed_factor*100}*H/10"
                overlay_filter = f"[0:v][wm]overlay={x_expr}:{y_expr}:shortest=1"
        else:
            # Static position or non-video
            if is_video:
                overlay_filter = f"[0:v][wm]overlay={overlay_position}:shortest=1"
            else:
                overlay_filter = f"[0:v][wm]overlay={overlay_position}"

        if is_video:
            # For video
            crf = max(0, min(51, 51 - int(quality * 51 / 100)))
            cmd = [
                "ffmpeg",
                "-i", str(input_path),
                "-stream_loop", "-1",  # Loop watermark infinitely
                "-i", str(watermark_image_path),
                "-y",
                "-filter_complex", f"{watermark_filter};{overlay_filter}",
                "-preset", "medium",
                "-crf", str(crf),
                "-c:a", "copy",
                "-shortest",  # Stop when shortest input ends (the main video)
                str(output_path)
            ]
        else:
            # For image - extract first frame of watermark and output single image
            cmd = [
                "ffmpeg",
                "-i", str(input_path),
                "-i", str(watermark_image_path),
                "-y",
                "-filter_complex", f"{watermark_filter};{overlay_filter}",
                "-frames:v", "1",  # Output only 1 frame
                "-q:v", str(max(1, min(31, int((100-quality)*31/100)))),
                str(output_path)
            ]

        logger.info(f"Running FFmpeg command for image watermark: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode != 0:
            raise Exception(f"FFmpeg failed: {result.stderr}")

        logger.info(f"Image watermark applied successfully to {output_path}")
        return output_path

    except subprocess.TimeoutExpired:
        raise Exception("FFmpeg timed out after 5 minutes")
    except Exception as e:
        raise Exception(f"Error applying image watermark: {str(e)}")

async def apply_watermark(
    input_path: Path,
    output_path: Path,
    watermark_text: str,
    position: str,
    size: int,
    transparency: int,
    quality: int,
    is_video: bool,
    rotation: int = 0,
    color: str = "white",
    effect: str = "none",
    effect_speed: int = 50,
) -> Path:
    """Applies a text watermark to an image or video using FFmpeg with advanced features."""
    logger.info(f"Applying watermark to {input_path} (video: {is_video}, color: {color}, effect: {effect}, rotation: {rotation}°)")

    # Position mapping for FFmpeg
    pos_map = {
        "top_left": "x=10:y=10",
        "top_center": "x=(w-text_w)/2:y=10",
        "top_right": "x=w-text_w-10:y=10",
        "mid_left": "x=10:y=(h-text_h)/2",
        "center": "x=(w-text_w)/2:y=(h-text_h)/2",
        "mid_right": "x=w-text_w-10:y=(h-text_h)/2",
        "bottom_left": "x=10:y=h-text_h-10",
        "bottom_center": "x=(w-text_w)/2:y=h-text_h-10",
        "bottom_right": "x=w-text_w-10:y=h-text_h-10",
    }

    # Calculate font size based on input size (more reasonable calculation)
    base_font_size = 48  # Base font size for 100% at 1080p
    font_size = max(12, int(base_font_size * (size / 100)))  # Minimum 12px

    # Transparency (alpha) for FFmpeg drawtext filter
    alpha = (100 - transparency) / 100.0

    # Color mapping
    color_map = {
        "white": "white",
        "black": "black",
        "red": "red",
        "blue": "blue",
        "green": "green",
        "yellow": "yellow",
        "cyan": "cyan",
        "magenta": "magenta",
        "orange": "orange",
        "purple": "purple",
    }
    font_color = color_map.get(color.lower(), "white")

    # Escape special characters in text for FFmpeg
    escaped_text = watermark_text.replace(":", "\\:").replace("'", "\\'")

    # Try to find a font file
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]

    font_file = None
    for font_path in font_paths:
        if Path(font_path).exists():
            font_file = font_path
            break

    # Build base drawtext filter
    if font_file:
        font_file = font_file.replace("\\", "/").replace(":", "\\:")
        base_filter = f"drawtext=fontfile='{font_file}':text='{escaped_text}'"
    else:
        logger.warning("No font file found, using FFmpeg default font")
        base_filter = f"drawtext=text='{escaped_text}'"

    # Add color and alpha
    base_filter += f":fontcolor={font_color}@{alpha}:fontsize={font_size}"

    # Handle effects for videos
    if is_video and effect != "none":
        # Calculate speed: higher effect_speed value = slower movement
        speed_factor = 100.0 / effect_speed

        if effect == "scroll_left":
            # Move from right to left
            drawtext_filter = f"{base_filter}:x=w-mod(t*{speed_factor}*w\\,w+text_w):y=(h-text_h)/2"
        elif effect == "scroll_right":
            # Move from left to right
            drawtext_filter = f"{base_filter}:x=-text_w+mod(t*{speed_factor}*w\\,w+text_w):y=(h-text_h)/2"
        elif effect == "scroll_up":
            # Move from bottom to top
            drawtext_filter = f"{base_filter}:x=(w-text_w)/2:y=h-mod(t*{speed_factor}*h\\,h+text_h)"
        elif effect == "scroll_down":
            # Move from top to bottom
            drawtext_filter = f"{base_filter}:x=(w-text_w)/2:y=-text_h+mod(t*{speed_factor}*h\\,h+text_h)"
        elif effect == "fade":
            # Fade in and out using alpha parameter
            pos_expr = pos_map.get(position, "x=w-text_w-10:y=h-text_h-10")
            # Fade formula: abs(sin(t)) oscillates between 0 and 1
            fade_alpha_expr = f"'abs(sin(t*{speed_factor}))*{alpha}'"
            # Rebuild filter with alpha as separate parameter
            if font_file:
                drawtext_filter = f"drawtext=fontfile='{font_file}':text='{escaped_text}':fontcolor={font_color}:alpha={fade_alpha_expr}:fontsize={font_size}:{pos_expr}"
            else:
                drawtext_filter = f"drawtext=text='{escaped_text}':fontcolor={font_color}:alpha={fade_alpha_expr}:fontsize={font_size}:{pos_expr}"
        elif effect == "pulse":
            # Pulse effect using alpha parameter (brightness pulsing)
            pos_expr = pos_map.get(position, "x=w-text_w-10:y=h-text_h-10")
            # Pulse between 50% and 100% alpha using proper FFmpeg syntax
            pulse_alpha_expr = f"'({alpha}*0.5)+({alpha}*0.5*abs(sin(t*{speed_factor})))'"
            # Need to rebuild filter with alpha as separate parameter
            if font_file:
                drawtext_filter = f"drawtext=fontfile='{font_file}':text='{escaped_text}':fontcolor={font_color}:alpha={pulse_alpha_expr}:fontsize={font_size}:{pos_expr}"
            else:
                drawtext_filter = f"drawtext=text='{escaped_text}':fontcolor={font_color}:alpha={pulse_alpha_expr}:fontsize={font_size}:{pos_expr}"
        elif effect == "wave":
            # Smooth wave motion
            pos_x = pos_map.get(position, "x=w-text_w-10:y=h-text_h-10").split(":")[0].replace("x=", "")
            amplitude = 20  # Pixels of vertical movement
            drawtext_filter = f"{base_filter}:x={pos_x}:y=((h-text_h)/2)+{amplitude}*sin(t*{speed_factor})"
        else:
            drawtext_filter = f"{base_filter}:{pos_map.get(position, 'x=w-text_w-10:y=h-text_h-10')}"
    else:
        # Static position (for images or no effect)
        drawtext_filter = f"{base_filter}:{pos_map.get(position, 'x=w-text_w-10:y=h-text_h-10')}"

    logger.info(f"Watermark settings - Size: {size}% → {font_size}px, Transparency: {transparency}%, Quality: {quality}%, Color: {font_color}, Effect: {effect}, Speed: {effect_speed}")

    ffmpeg_command = [
        "ffmpeg",
        "-i", str(input_path),
        "-y",  # Overwrite output file
    ]

    if is_video:
        # For video, apply drawtext filter to video stream
        crf_value = int(51 - (quality * 0.51))
        ffmpeg_command.extend([
            "-vf", drawtext_filter,
            "-preset", "medium",
            "-crf", str(crf_value),
            "-c:a", "copy",  # Copy audio stream without re-encoding
            str(output_path)
        ])
    else:
        # For images, apply drawtext filter
        ffmpeg_command.extend([
            "-vf", drawtext_filter,
            "-q:v", str(int((100 - quality) * 0.31)),  # Quality for JPEG (2-31, lower is better)
            str(output_path)
        ])

    try:
        logger.info(f"Running FFmpeg command: {' '.join(ffmpeg_command)}")
        process = await asyncio.create_subprocess_exec(
            *ffmpeg_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            logger.error(f"FFmpeg error: {stderr.decode()}")
            raise Exception(f"FFmpeg failed: {stderr.decode()}")

        logger.info(f"Watermark applied successfully to {output_path}")
        return output_path
    except FileNotFoundError:
        raise Exception("FFmpeg executable not found. Please ensure FFmpeg is installed and added to your system's PATH.")


# --- Helpers ---
def make_main_keyboard():
    kb = [
        [InlineKeyboardButton("➕ Add Channel/Group", callback_data="add_channel")],
        [InlineKeyboardButton("📂 My Channels & Groups", callback_data="show_channels")],
    ]
    return InlineKeyboardMarkup(kb)


def make_channel_settings_keyboard(channel_id: int):
    kb = [
        [InlineKeyboardButton("🔘 Auto Button", callback_data=f"channel_settings_auto_button_{channel_id}")],
        [InlineKeyboardButton("💬 Auto Captions", callback_data=f"channel_settings_auto_captions_{channel_id}")],
        [InlineKeyboardButton("❤️ Auto Reactions", callback_data=f"channel_settings_reactions_{channel_id}")],
        [InlineKeyboardButton("🖼️ Auto Watermark", callback_data=f"channel_settings_auto_watermark_{channel_id}")],
        [InlineKeyboardButton("🗑️ Remove channel", callback_data=f"remove_channel_{channel_id}")],
        [InlineKeyboardButton("⪡ Back to channel list", callback_data="show_channels")],
    ]
    return InlineKeyboardMarkup(kb)


def make_channel_list_keyboard(user_id: int):
    channels = USER_CHANNELS.get(user_id, [])
    if not channels:
        kb = [
            [InlineKeyboardButton("➕ Add a channel/group", callback_data="add_channel")],
            [InlineKeyboardButton("⪡ Back to Main Menu", callback_data="back_to_main")],
        ]
        return InlineKeyboardMarkup(kb)

    kb = []
    # Create a button for each channel/group with proper icon
    for ch in channels:
        title = ch.get("title") or ch.get("username") or str(ch.get("id"))
        chat_type = ch.get("type", "channel")  # Default to channel if type not specified

        # Add appropriate icon based on chat type
        if chat_type == "group" or chat_type == "supergroup":
            icon = "👥"
        else:
            icon = "📢"

        data = f"select_{ch['id']}"
        kb.append([InlineKeyboardButton(f"{icon} {title}", callback_data=data)])

    # Add control buttons at the bottom
    kb.append([InlineKeyboardButton("➕ Add another channel/group", callback_data="add_channel")])
    kb.append([InlineKeyboardButton("⪡ Back to Main Menu", callback_data="back_to_main")])
    return InlineKeyboardMarkup(kb)


# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"👋 Hi {user.first_name or 'there'}!\n\n"
        f"🤖 *I'm your Channel & Group Manager Bot*\n\n"
        f"I can help you manage your Telegram channels and groups with:\n"
        f"• 🔘 Auto Buttons\n"
        f"• 💬 Auto Captions\n"
        f"• ❤️ Auto Reactions\n"
        f"• 🖼️ Auto Watermarks\n\n"
        f"📌 *Get Started:*\n"
        f"Press *Add channel* and then forward any message from your channel or group to register it.\n\n"
        f"💡 *Why forward?* When you forward a message, I receive the chat information needed to manage it!"
    )
    try:
        await update.message.reply_markdown(text, reply_markup=make_main_keyboard())
    except TelegramError as e:
        logger.error(f"Error sending start message: {e}")


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    try:
        if data == "add_channel":
            bot_username = (await context.bot.get_me()).username
            permissions_list = (
                "change_info+"
                "post_messages+"
                "edit_messages+"
                "delete_messages+"
                "invite_users+"
                "pin_messages+"
                "manage_video_chats+"
                "post_stories+"
                "edit_stories+"
                "delete_stories"
            )
            c_url = f"https://t.me/{bot_username}?startchannel&admin={permissions_list}"
            g_url = f"https://t.me/{bot_username}?startgroup&admin={permissions_list}"

            text = (
                "📢 *ADD ME TO YOUR CHANNEL OR GROUP*\n\n"
                "Choose where you want to add me:\n\n"
                "🔹 *Channel* - For broadcasting messages\n"
                "🔹 *Group* - For group discussions\n\n"
                "⚠️ *Important*: After adding me, you must forward a message from the channel/group to this chat to complete the registration."
            )
            kb = [
                [
                    InlineKeyboardButton("📢 Add to Channel", url=c_url),
                    InlineKeyboardButton("👥 Add to Group", url=g_url)
                ],
                [InlineKeyboardButton("⪡ Back to Main Menu", callback_data="back_to_main")]
            ]
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="Markdown",
            )
            return

        if data == "show_channels":
            # Show the user's saved channels/groups
            channels = USER_CHANNELS.get(user_id, [])

            if channels:
                # Count channels and groups
                channel_count = sum(1 for c in channels if c.get("type", "channel") == "channel")
                group_count = sum(1 for c in channels if c.get("type", "channel") in ["group", "supergroup"])

                text = f"📂 *YOUR SAVED CHANNELS & GROUPS*\n\n"
                text += f"📢 Channels: {channel_count}\n"
                text += f"👥 Groups: {group_count}\n"
                text += f"📊 Total: {len(channels)}\n\n"
                text += "Select one to manage its settings:"
            else:
                text = "❌ *NO CHANNELS OR GROUPS SAVED*\n\nYou haven't added any channels or groups yet.\n\nClick the button below to add one!"

            await query.edit_message_text(
                text, 
                reply_markup=make_channel_list_keyboard(user_id),
                parse_mode="Markdown"
            )
            return

        if data.startswith("channel_settings_auto_button_"):
            ch_id = int(data.split("_")[-1])
            await send_auto_button_settings(update, context, user_id, ch_id)
            return

        if data.startswith("channel_settings_auto_watermark_"):
            ch_id = int(data.split("_")[-1])
            await send_auto_watermark_settings(update, context, user_id, ch_id)
            return

        if data.startswith("toggle_auto_watermark_status_"):
            ch_id = int(data.split("_")[-1])
            channels = USER_CHANNELS.get(user_id, [])
            chosen = next((c for c in channels if c["id"] == ch_id), None)

            if chosen:
                current_status = chosen.get("auto_watermark", {}).get("status", "inactive")
                new_status = "active" if current_status == "inactive" else "inactive"
                chosen.setdefault("auto_watermark", {})["status"] = new_status
                save_data()

            await send_auto_watermark_settings(update, context, user_id, ch_id)
            return

        if data.startswith("change_auto_watermark_config_"):
            ch_id = int(data.split("_")[-1])
            text = f"""🎯 WATERMARK CONFIGURATION

You can set the watermark in three ways:

1️⃣ **Text Watermark** (e.g., "© YourChannel")
   → Send text message

2️⃣ **Image Watermark** (logo, PNG with transparency)
   → Send photo/image

3️⃣ **GIF Watermark** (animated logo)
   → Send GIF/animation

Please send your watermark now:
"""
            kb = [
                [InlineKeyboardButton("⪡ Back", callback_data=f"channel_settings_auto_watermark_{ch_id}")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            context.user_data['next_step'] = f'set_auto_watermark_config_{ch_id}'
            return

        if data.startswith("set_watermark_position_"):
            ch_id = int(data.split("_")[-1])
            await send_watermark_position_settings(update, context, user_id, ch_id)
            return

        if data.startswith("set_watermark_size_"):
            ch_id = int(data.split("_")[-1])
            await send_watermark_size_settings(update, context, user_id, ch_id)
            return

        if data.startswith("set_watermark_transparency_"):
            ch_id = int(data.split("_")[-1])
            await send_watermark_transparency_settings(update, context, user_id, ch_id)
            return

        if data.startswith("set_watermark_quality_"):
            ch_id = int(data.split("_")[-1])
            await send_watermark_quality_settings(update, context, user_id, ch_id)
            return

        if data.startswith("set_watermark_rotation_"):
            ch_id = int(data.split("_")[-1])
            await send_watermark_rotation_settings(update, context, user_id, ch_id)
            return

        if data.startswith("set_rot_"):
            parts = data.split("_")
            if parts[2] == "custom":
                ch_id = int(parts[3])
                context.user_data['next_step'] = f'set_watermark_rotation_value_{ch_id}'
                kb = [[InlineKeyboardButton("⪡ Cancel", callback_data=f"channel_settings_auto_watermark_{ch_id}")]]
                await query.edit_message_text(
                    "Please send the rotation angle (0-360 degrees):",
                    reply_markup=InlineKeyboardMarkup(kb)
                )
            else:
                rotation = int(parts[2])
                ch_id = int(parts[3])

                channels = USER_CHANNELS.get(user_id, [])
                chosen = next((c for c in channels if c["id"] == ch_id), None)

                if chosen:
                    chosen.setdefault("auto_watermark", {})["rotation"] = rotation
                    save_data()
                    await query.answer(f"✅ Rotation set to {rotation}°")

                await send_auto_watermark_settings(update, context, user_id, ch_id)
            return

        if data.startswith("set_watermark_color_"):
            ch_id = int(data.split("_")[-1])
            await send_watermark_color_settings(update, context, user_id, ch_id)
            return

        if data.startswith("set_color_"):
            parts = data.split("_")
            color = parts[2]
            ch_id = int(parts[3])

            channels = USER_CHANNELS.get(user_id, [])
            chosen = next((c for c in channels if c["id"] == ch_id), None)

            if chosen:
                chosen.setdefault("auto_watermark", {})["color"] = color
                save_data()
                await query.answer(f"✅ Color set to {color}")

            await send_auto_watermark_settings(update, context, user_id, ch_id)
            return

        if data.startswith("set_watermark_effect_"):
            ch_id = int(data.split("_")[-1])
            await send_watermark_effect_settings(update, context, user_id, ch_id)
            return

        if data.startswith("set_effect_"):
            parts = data.split("_")

            if parts[2] == "speed":
                ch_id = int(parts[3])
                context.user_data['next_step'] = f'set_effect_speed_value_{ch_id}'
                kb = [[InlineKeyboardButton("⪡ Cancel", callback_data=f"set_watermark_effect_{ch_id}")]]
                text = """Please send effect speed value:

**Speed Guide:**
• 5-10: Very Fast
• 15-20: Fast
• 35-50: Medium (default)
• 70: Slow
• 100: Very Slow

Higher value = Slower movement"""
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            else:
                # Handle effect types with underscores (scroll_left, scroll_right, etc)
                effect = "_".join(parts[2:-1])
                ch_id = int(parts[-1])

                channels = USER_CHANNELS.get(user_id, [])
                chosen = next((c for c in channels if c["id"] == ch_id), None)

                if chosen:
                    chosen.setdefault("auto_watermark", {})["effect"] = effect
                    save_data()
                    await query.answer(f"✅ Effect set to {effect}")

                await send_auto_watermark_settings(update, context, user_id, ch_id)
            return

        if data.startswith("set_wm_pos_"):
            parts = data.split("_")
            position_key = "_".join(parts[3:-1]) # e.g., "top_left", "bottom_right"
            ch_id = int(parts[-1])

            channels = USER_CHANNELS.get(user_id, [])
            chosen = next((c for c in channels if c["id"] == ch_id), None)

            if chosen:
                current_position = chosen.get("auto_watermark", {}).get("position", "bottom_right")
                if current_position == position_key:
                    await query.answer(f"Position is already set to {position_key.replace('_', ' ')}.")
                    return
                chosen.setdefault("auto_watermark", {})["position"] = position_key
                save_data()

            await send_auto_watermark_settings(update, context, user_id, ch_id)
            return

        if data.startswith("toggle_auto_button_status_"):
            ch_id = int(data.split("_")[-1])
            channels = USER_CHANNELS.get(user_id, [])
            chosen = next((c for c in channels if c["id"] == ch_id), None)

            if chosen:
                current_status = chosen.get("auto_button", {}).get("status", "inactive")
                new_status = "active" if current_status == "inactive" else "inactive"
                chosen.setdefault("auto_button", {})["status"] = new_status
                save_data()

            await send_auto_button_settings(update, context, user_id, ch_id)
            return

        if data.startswith("change_auto_button_config_"):
            ch_id = int(data.split("_")[-1])
            text = f"""🎯 BUTTON CONFIGURATION

📝 Add buttons that will automatically appear below your channel posts.

✨ Example:
• Insert a single button:
`Button text - t.me/LinkExample`

• Insert multiple buttons in a single line:
`Button text - t.me/Link1 && Button text - t.me/Link2`

• Insert a button that displays a popup:
`Button text - popup: Text of the popup`

Please send me the new button configuration."""
            kb = [
                [InlineKeyboardButton("⪡ Back", callback_data=f"channel_settings_auto_button_{ch_id}")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            context.user_data['next_step'] = f'set_auto_button_config_{ch_id}'
            return

        if data.startswith("channel_settings_auto_captions_"):
            ch_id = int(data.split("_")[-1])
            await send_auto_caption_settings(update, context, user_id, ch_id)
            return

        if data.startswith("toggle_auto_caption_status_"):
            ch_id = int(data.split("_")[-1])
            channels = USER_CHANNELS.get(user_id, [])
            chosen = next((c for c in channels if c["id"] == ch_id), None)

            if chosen:
                current_status = chosen.get("auto_captions", {}).get("status", "inactive")
                new_status = "active" if current_status == "inactive" else "inactive"
                chosen.setdefault("auto_captions", {})["status"] = new_status
                save_data()

            await send_auto_caption_settings(update, context, user_id, ch_id)
            return

        if data.startswith("change_auto_caption_config_"):
            ch_id = int(data.split("_")[-1])
            text = f"""🎯 Captions CONFIGURATION

📝 Add Captions that will automatically appear below your channel posts.

✨ Example:
Please send me the new Captions configuration.

To format text in Telegram, you can use the following styles:
- MarkdownV2
- HTML
- Markdown (legacy)

Your caption will be attached to your post."""
            kb = [
                [InlineKeyboardButton("⪡ Back", callback_data=f"channel_settings_auto_captions_{ch_id}")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            context.user_data['next_step'] = f'set_auto_caption_config_{ch_id}'
            return

        if data.startswith("channel_settings_reactions_"):
            ch_id = int(data.split("_")[-1])
            channels = USER_CHANNELS.get(user_id, [])
            chosen = next((c for c in channels if c["id"] == ch_id), None)

            if not chosen:
                await query.edit_message_text("Channel not found.", reply_markup=make_channel_list_keyboard(user_id))
                return

            title = chosen.get("title") or chosen.get("username") or str(chosen["id"])
            auto_reactions_status = chosen.get("auto_reactions", {}).get("status", "inactive")

            status_emoji = "🟢" if auto_reactions_status == "active" else "🔴"
            status_text = f"Status: {status_emoji} Auto Reactions {auto_reactions_status.upper()}"

            text = (
                f"⚙️ CHANNEL: {title}\n\n"
                "▲ ═══════════════════════ ▲\n\n"
                f"{status_text}\n\n"
                "▲ ═══════════════════════ ▲\n\n"
                "• How it works: I automatically add fun emoji reactions to posts and messages.\n"
                "• Forced subscription: You must join both channels before using the bot.\n\n"
                "What would you like to do? 👇"
            )

            kb = [
                [
                    InlineKeyboardButton("Enabled ✅", callback_data=f"toggle_auto_reactions_active_{ch_id}"),
                    InlineKeyboardButton("Disable ❌", callback_data=f"toggle_auto_reactions_inactive_{ch_id}")
                ],
                [InlineKeyboardButton("⪡ Back", callback_data=f"select_{ch_id}")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        if data.startswith("toggle_auto_reactions_"):
            parts = data.split("_")
            new_status = parts[3]
            ch_id = int(parts[4])

            channels = USER_CHANNELS.get(user_id, [])
            chosen = next((c for c in channels if c["id"] == ch_id), None)

            if not chosen:
                await query.edit_message_text("Channel not found.", reply_markup=make_channel_list_keyboard(user_id))
                return

            current_status = chosen.get("auto_reactions", {}).get("status", "inactive")
            if current_status == new_status:
                await query.answer(f"Auto reactions are already {new_status}.")
                return

            chosen.setdefault("auto_reactions", {})["status"] = new_status
            save_data()

            title = chosen.get("title") or chosen.get("username") or str(chosen["id"])
            auto_reactions_status = new_status

            status_emoji = "🟢" if auto_reactions_status == "active" else "🔴"
            status_text = f"Status: {status_emoji} Auto Reactions {auto_reactions_status.upper()}"

            text = (
                f"⚙️ CHANNEL: {title}\n\n"
                "▲ ═══════════════════════ ▲\n\n"
                f"{status_text}\n\n"
                "▲ ═══════════════════════ ▲\n\n"
                "• How it works: I automatically add fun emoji reactions to posts and messages.\n"
                "• Forced subscription: You must join both channels before using the bot.\n\n"
                "What would you like to do? 👇"
            )

            kb = [
                [
                    InlineKeyboardButton("Enabled ✅", callback_data=f"toggle_auto_reactions_active_{ch_id}"),
                    InlineKeyboardButton("Disable ❌", callback_data=f"toggle_auto_reactions_inactive_{ch_id}")
                ],
                [InlineKeyboardButton("⪡ Back", callback_data=f"select_{ch_id}")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        if data.startswith("remove_channel_"):
            ch_id_to_remove = int(data.split("_", 2)[2])
            channels = USER_CHANNELS.get(user_id, [])
            chosen = next((c for c in channels if c["id"] == ch_id_to_remove), None)
            if not chosen:
                await query.edit_message_text("Channel not found.", reply_markup=make_channel_list_keyboard(user_id))
                return

            title = chosen.get("title") or chosen.get("username") or str(chosen["id"])

            # Ask for confirmation to remove the channel
            kb = [
                [
                    InlineKeyboardButton("✅ Yes, remove it", callback_data=f"remove_yes_{ch_id_to_remove}"),
                    InlineKeyboardButton("⪡ Back", callback_data=f"select_{ch_id_to_remove}"),
                ]
            ]
            await query.edit_message_text(
                f"Do you want to remove the channel *{title}*?",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            return

        if data.startswith("select_"):
            ch_id = int(data.split("_", 1)[1])
            channels = USER_CHANNELS.get(user_id, [])
            chosen = next((c for c in channels if c["id"] == ch_id), None)

            if not chosen:
                await query.edit_message_text("Channel not found.", reply_markup=make_channel_list_keyboard(user_id))
                return

            title = chosen.get("title") or chosen.get("username") or str(chosen["id"])
            chat_type = chosen.get("type", "channel")
            chat_icon = "📢" if chat_type == "channel" else "👥"
            chat_type_name = "Channel" if chat_type == "channel" else "Group"

            # Show channel-specific settings
            text = (
                f"{chat_icon} *{chat_type_name} Settings*\n\n"
                f"*Name:* {title}\n"
                f"*ID:* `{chosen['id']}`\n\n"
                f"Select a feature to configure:"
            )

            await query.edit_message_text(
                text,
                parse_mode="Markdown",
                reply_markup=make_channel_settings_keyboard(ch_id)
            )
            return

        if data.startswith("remove_yes_"):
            ch_id_to_remove = int(data.split("_", 2)[2])
            # Remove the channel from the user's list
            if user_id in USER_CHANNELS:
                USER_CHANNELS[user_id] = [ch for ch in USER_CHANNELS[user_id] if ch["id"] != ch_id_to_remove]
                save_data()  # Persist the change

            await query.edit_message_text("Channel removed. Here is your updated list:", reply_markup=make_channel_list_keyboard(user_id))
            return

        if data == "back_to_main":
            # Go back to the main menu
            user = update.effective_user
            text = (
                f"Hi {user.first_name or 'there'}! I can help you register a channel with me.\n\n"
                "What would you like to do next?"
            )
            await query.edit_message_text(text, reply_markup=make_main_keyboard())
            return

        # fallback
        await query.edit_message_text("Unknown action.")

    except TelegramError as e:
        logger.error(f"Error in button_router: {e}")


async def send_auto_button_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, ch_id: int):
    channels = USER_CHANNELS.get(user_id, [])
    chosen = next((c for c in channels if c["id"] == ch_id), None)

    if not chosen:
        if update.callback_query:
            await update.callback_query.edit_message_text("Channel not found.", reply_markup=make_channel_list_keyboard(user_id))
        else:
            await update.message.reply_text("Channel not found.")
        return

    title = chosen.get("title") or chosen.get("username") or str(chosen["id"])
    auto_button_status = chosen.get("auto_button", {}).get("status", "inactive")
    auto_button_config = chosen.get("auto_button", {}).get("config", "Not set")

    status_emoji = "🟢" if auto_button_status == "active" else "🔴"

    text = f"""⚙️ CHANNEL: {title}

▲ ═══════════════════════ ▲

Status: {status_emoji} Auto button {auto_button_status.upper()}

▲ ═══════════════════════ ▲

🎯 CURRENT BUTTON CONFIGURATION
`{auto_button_config}`

▼ ═══════════════════════ ▼
What would you like to do? 👇"""

    toggle_status_text = "Deactivate" if auto_button_status == "active" else "Activate"
    kb = [
        [InlineKeyboardButton(f"{'🔴' if auto_button_status == 'active' else '🟢'} {toggle_status_text} Auto Button", callback_data=f"toggle_auto_button_status_{ch_id}")],
        [InlineKeyboardButton("🔧 CHANGE BUTTON", callback_data=f"change_auto_button_config_{ch_id}")],
        [InlineKeyboardButton("⪡ Back", callback_data=f"select_{ch_id}")]
    ]

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def send_auto_caption_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, ch_id: int):
    channels = USER_CHANNELS.get(user_id, [])
    chosen = next((c for c in channels if c["id"] == ch_id), None)

    if not chosen:
        if update.callback_query:
            await update.callback_query.edit_message_text("Channel not found.", reply_markup=make_channel_list_keyboard(user_id))
        else:
            await update.message.reply_text("Channel not found.")
        return

    title = chosen.get("title") or chosen.get("username") or str(chosen["id"])
    auto_caption_status = chosen.get("auto_captions", {}).get("status", "inactive")
    auto_caption_config = chosen.get("auto_captions", {}).get("config", "Not set")

    status_emoji = "🟢" if auto_caption_status == "active" else "🔴"

    text = f"""⚙️ CHANNEL: {title}

▲ ═══════════════════════ ▲

Status: {status_emoji} Auto Captions {auto_caption_status.upper()}

▲ ═══════════════════════ ▲

🎯 CURRENT CAPTION
`{auto_caption_config}`

▼ ═══════════════════════ ▼
What would you like to do? 👇"""

    toggle_status_text = "Deactivate" if auto_caption_status == "active" else "Activate"
    kb = [
        [InlineKeyboardButton(f"{'🔴' if auto_caption_status == 'active' else '🟢'} {toggle_status_text} Auto Captions", callback_data=f"toggle_auto_caption_status_{ch_id}")],
        [InlineKeyboardButton("✍️ CHANGE CAPTION", callback_data=f"change_auto_caption_config_{ch_id}")],
        [InlineKeyboardButton("⪡ Back", callback_data=f"select_{ch_id}")]
    ]

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def send_auto_watermark_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, ch_id: int):
    channels = USER_CHANNELS.get(user_id, [])
    chosen = next((c for c in channels if c["id"] == ch_id), None)

    if not chosen:
        if update.callback_query:
            await update.callback_query.edit_message_text("Channel not found.", reply_markup=make_channel_list_keyboard(user_id))
        else:
            await update.message.reply_text("Channel not found.")
        return

    title = chosen.get("title") or chosen.get("username") or str(chosen["id"])
    auto_watermark_status = chosen.get("auto_watermark", {}).get("status", "inactive")
    watermark_type = chosen.get("auto_watermark", {}).get("type", "text")
    auto_watermark_config = chosen.get("auto_watermark", {}).get("config", "Not set")
    watermark_position = chosen.get("auto_watermark", {}).get("position", "bottom_right")
    watermark_size = chosen.get("auto_watermark", {}).get("size", 50)
    watermark_transparency = chosen.get("auto_watermark", {}).get("transparency", 50)
    watermark_quality = chosen.get("auto_watermark", {}).get("quality", 75)
    watermark_rotation = chosen.get("auto_watermark", {}).get("rotation", 0)
    watermark_color = chosen.get("auto_watermark", {}).get("color", "white")
    watermark_effect = chosen.get("auto_watermark", {}).get("effect", "none")
    watermark_effect_speed = chosen.get("auto_watermark", {}).get("effect_speed", 50)

    status_emoji = "🟢" if auto_watermark_status == "active" else "🔴"
    type_emoji = "📝" if watermark_type == "text" else ("🖼️" if watermark_type == "image" else "✨")

    text = f"""⚙️ CHANNEL: {title}

▲ ═══════════════════════ ▲

Status: {status_emoji} Auto Watermark {auto_watermark_status.upper()}

▲ ═══════════════════════ ▲

🎯 CURRENT WATERMARK CONFIGURATION
Type: {type_emoji} {watermark_type.upper()}
Text: `{auto_watermark_config}`
Position: `{watermark_position}`
Size: `{watermark_size}%`
Transparency: `{watermark_transparency}%`
Quality: `{watermark_quality}%`"""

    # Add text-specific settings
    if watermark_type == "text":
        text += f"""
Rotation: `{watermark_rotation}°`
Color: `{watermark_color}`
Effect: `{watermark_effect}` (Speed: {watermark_effect_speed})"""

    text += "\n\n▼ ═══════════════════════ ▼\nWhat would you like to do? 👇"

    toggle_status_text = "Deactivate" if auto_watermark_status == "active" else "Activate"
    kb = [
        [InlineKeyboardButton(f"{'🔴' if auto_watermark_status == 'active' else '🟢'} {toggle_status_text} Auto Watermark", callback_data=f"toggle_auto_watermark_status_{ch_id}")],
        [InlineKeyboardButton("🔧 CHANGE TEXT", callback_data=f"change_auto_watermark_config_{ch_id}")],
        [
            InlineKeyboardButton("📍 Position", callback_data=f"set_watermark_position_{ch_id}"),
            InlineKeyboardButton("📏 Size", callback_data=f"set_watermark_size_{ch_id}")
        ],
        [
            InlineKeyboardButton("🔍 Transparency", callback_data=f"set_watermark_transparency_{ch_id}"),
            InlineKeyboardButton("⚙️ Quality", callback_data=f"set_watermark_quality_{ch_id}")
        ],
        [
            InlineKeyboardButton("📐 Rotation", callback_data=f"set_watermark_rotation_{ch_id}"),
            InlineKeyboardButton("🎨 Text Color", callback_data=f"set_watermark_color_{ch_id}")
        ],
        [InlineKeyboardButton("✨ Effects & Speed", callback_data=f"set_watermark_effect_{ch_id}")],
        [InlineKeyboardButton("⪡ Back", callback_data=f"select_{ch_id}")]
    ]

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        except TelegramError as e:
            if "Query is too old" not in str(e):
                logger.error(f"Error updating watermark settings: {e}")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def send_watermark_position_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, ch_id: int):
    channels = USER_CHANNELS.get(user_id, [])
    chosen = next((c for c in channels if c["id"] == ch_id), None)

    if not chosen:
        if update.callback_query:
            await update.callback_query.edit_message_text("Channel not found.", reply_markup=make_channel_list_keyboard(user_id))
        else:
            await update.message.reply_text("Channel not found.")
        return

    title = chosen.get("title") or chosen.get("username") or str(chosen["id"])
    current_position = chosen.get("auto_watermark", {}).get("position", "bottom_right")

    text = f"""⚙️ CHANNEL: {title}

▲ ═══════════════════════ ▲

Current Watermark Position: `{current_position}`

▼ ═══════════════════════ ▼
Choose the location of the watermark:"""

    kb = [
        [
            InlineKeyboardButton("↖️ Top-Left", callback_data=f"set_wm_pos_top_left_{ch_id}"),
            InlineKeyboardButton("⬆️ Top-Center", callback_data=f"set_wm_pos_top_center_{ch_id}"),
            InlineKeyboardButton("↗️ Top-Right", callback_data=f"set_wm_pos_top_right_{ch_id}")
        ],
        [
            InlineKeyboardButton("⬅️ Mid-Left", callback_data=f"set_wm_pos_mid_left_{ch_id}"),
            InlineKeyboardButton("⏺️ Center", callback_data=f"set_wm_pos_center_{ch_id}"),
            InlineKeyboardButton("➡️ Mid-Right", callback_data=f"set_wm_pos_mid_right_{ch_id}")
        ],
        [
            InlineKeyboardButton("↙️ Bottom-Left", callback_data=f"set_wm_pos_bottom_left_{ch_id}"),
            InlineKeyboardButton("⬇️ Bottom-Center", callback_data=f"set_wm_pos_bottom_center_{ch_id}"),
            InlineKeyboardButton("↘️ Bottom-Right", callback_data=f"set_wm_pos_bottom_right_{ch_id}")
        ],
        [InlineKeyboardButton("⪡ Back to Watermark Settings", callback_data=f"channel_settings_auto_watermark_{ch_id}")]
    ]

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def send_watermark_size_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, ch_id: int):
    channels = USER_CHANNELS.get(user_id, [])
    chosen = next((c for c in channels if c["id"] == ch_id), None)

    if not chosen:
        if update.callback_query:
            await update.callback_query.edit_message_text("Channel not found.", reply_markup=make_channel_list_keyboard(user_id))
        else:
            await update.message.reply_text("Channel not found.")
        return

    title = chosen.get("title") or chosen.get("username") or str(chosen["id"])
    current_size = chosen.get("auto_watermark", {}).get("size", 50)

    text = f"""⚙️ CHANNEL: {title}

▲ ═══════════════════════ ▲

Current Watermark Size: `{current_size}%`

▼ ═══════════════════════ ▼
Please send me the new watermark size (a number between 1 and 100).
This represents the percentage of the original media's size."""

    kb = [
        [InlineKeyboardButton("⪡ Back to Watermark Settings", callback_data=f"channel_settings_auto_watermark_{ch_id}")]
    ]

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    context.user_data['next_step'] = f'set_watermark_size_value_{ch_id}'


async def send_watermark_transparency_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, ch_id: int):
    channels = USER_CHANNELS.get(user_id, [])
    chosen = next((c for c in channels if c["id"] == ch_id), None)

    if not chosen:
        if update.callback_query:
            await update.callback_query.edit_message_text("Channel not found.", reply_markup=make_channel_list_keyboard(user_id))
        else:
            await update.message.reply_text("Channel not found.")
        return

    title = chosen.get("title") or chosen.get("username") or str(chosen["id"])
    current_transparency = chosen.get("auto_watermark", {}).get("transparency", 50)

    text = f"""⚙️ CHANNEL: {title}

▲ ═══════════════════════ ▲

Current Watermark Transparency: `{current_transparency}%`

▼ ═══════════════════════ ▼
Please send me the new watermark transparency (a number between 0 and 95).
Where 0% is not transparent, and 95% is barely noticeable."""

    kb = [
        [InlineKeyboardButton("⪡ Back to Watermark Settings", callback_data=f"channel_settings_auto_watermark_{ch_id}")]
    ]

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    context.user_data['next_step'] = f'set_watermark_transparency_value_{ch_id}'


async def send_watermark_quality_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, ch_id: int):
    channels = USER_CHANNELS.get(user_id, [])
    chosen = next((c for c in channels if c["id"] == ch_id), None)

    if not chosen:
        if update.callback_query:
            await update.callback_query.edit_message_text("Channel not found.", reply_markup=make_channel_list_keyboard(user_id))
        else:
            await update.message.reply_text("Channel not found.")
        return

    title = chosen.get("title") or chosen.get("username") or str(chosen["id"])
    current_quality = chosen.get("auto_watermark", {}).get("quality", 75)

    text = f"""⚙️ CHANNEL: {title}

▲ ═══════════════════════ ▲

Current Watermark Quality: `{current_quality}%`

▼ ═══════════════════════ ▼
Please send me the new watermark quality (a number between 1 and 100).

If you choose less than 30, the quality will be worse, but the finished file size will also be smaller.
If more than 60, then the size of the finished file may be larger than the original one.

In most cases this setting is not needed and the result will be good with the default setting."""

    kb = [
        [InlineKeyboardButton("⪡ Back to Watermark Settings", callback_data=f"channel_settings_auto_watermark_{ch_id}")]
    ]

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    context.user_data['next_step'] = f'set_watermark_quality_value_{ch_id}'


async def send_watermark_rotation_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, ch_id: int):
    channels = USER_CHANNELS.get(user_id, [])
    chosen = next((c for c in channels if c["id"] == ch_id), None)

    if not chosen:
        if update.callback_query:
            await update.callback_query.edit_message_text("Channel not found.", reply_markup=make_channel_list_keyboard(user_id))
        else:
            await update.message.reply_text("Channel not found.")
        return

    title = chosen.get("title") or chosen.get("username") or str(chosen["id"])
    current_rotation = chosen.get("auto_watermark", {}).get("rotation", 0)

    text = f"""⚙️ CHANNEL: {title}

▲ ═══════════════════════ ▲

Current Rotation: `{current_rotation}°`

▼ ═══════════════════════ ▼
Select rotation angle or send custom value (0-360 degrees):

• 0° - Horizontal (default)
• 45° - Diagonal
• 90° - Vertical
• 180° - Upside down
• 270° - Vertical (opposite)"""

    kb = [
        [
            InlineKeyboardButton("0°", callback_data=f"set_rot_0_{ch_id}"),
            InlineKeyboardButton("45°", callback_data=f"set_rot_45_{ch_id}"),
            InlineKeyboardButton("90°", callback_data=f"set_rot_90_{ch_id}")
        ],
        [
            InlineKeyboardButton("135°", callback_data=f"set_rot_135_{ch_id}"),
            InlineKeyboardButton("180°", callback_data=f"set_rot_180_{ch_id}"),
            InlineKeyboardButton("270°", callback_data=f"set_rot_270_{ch_id}")
        ],
        [InlineKeyboardButton("✏️ Custom Angle", callback_data=f"set_rot_custom_{ch_id}")],
        [InlineKeyboardButton("⪡ Back to Watermark Settings", callback_data=f"channel_settings_auto_watermark_{ch_id}")]
    ]

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def send_watermark_color_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, ch_id: int):
    channels = USER_CHANNELS.get(user_id, [])
    chosen = next((c for c in channels if c["id"] == ch_id), None)

    if not chosen:
        if update.callback_query:
            await update.callback_query.edit_message_text("Channel not found.", reply_markup=make_channel_list_keyboard(user_id))
        else:
            await update.message.reply_text("Channel not found.")
        return

    title = chosen.get("title") or chosen.get("username") or str(chosen["id"])
    current_color = chosen.get("auto_watermark", {}).get("color", "white")

    text = f"""⚙️ CHANNEL: {title}

▲ ═══════════════════════ ▲

Current Color: `{current_color}`

▼ ═══════════════════════ ▼
Choose watermark text color:"""

    kb = [
        [
            InlineKeyboardButton("⚪ White", callback_data=f"set_color_white_{ch_id}"),
            InlineKeyboardButton("⚫ Black", callback_data=f"set_color_black_{ch_id}")
        ],
        [
            InlineKeyboardButton("🔴 Red", callback_data=f"set_color_red_{ch_id}"),
            InlineKeyboardButton("🔵 Blue", callback_data=f"set_color_blue_{ch_id}")
        ],
        [
            InlineKeyboardButton("🟢 Green", callback_data=f"set_color_green_{ch_id}"),
            InlineKeyboardButton("🟡 Yellow", callback_data=f"set_color_yellow_{ch_id}")
        ],
        [
            InlineKeyboardButton("🟣 Purple", callback_data=f"set_color_purple_{ch_id}"),
            InlineKeyboardButton("🟠 Orange", callback_data=f"set_color_orange_{ch_id}")
        ],
        [
            InlineKeyboardButton("🔵 Cyan", callback_data=f"set_color_cyan_{ch_id}"),
            InlineKeyboardButton("🟣 Magenta", callback_data=f"set_color_magenta_{ch_id}")
        ],
        [InlineKeyboardButton("⪡ Back to Watermark Settings", callback_data=f"channel_settings_auto_watermark_{ch_id}")]
    ]

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def send_watermark_effect_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, ch_id: int):
    channels = USER_CHANNELS.get(user_id, [])
    chosen = next((c for c in channels if c["id"] == ch_id), None)

    if not chosen:
        if update.callback_query:
            await update.callback_query.edit_message_text("Channel not found.", reply_markup=make_channel_list_keyboard(user_id))
        else:
            await update.message.reply_text("Channel not found.")
        return

    title = chosen.get("title") or chosen.get("username") or str(chosen["id"])
    current_effect = chosen.get("auto_watermark", {}).get("effect", "none")
    current_speed = chosen.get("auto_watermark", {}).get("effect_speed", 50)
    watermark_type = chosen.get("auto_watermark", {}).get("type", "text")

    text = f"""⚙️ CHANNEL: {title}

▲ ═══════════════════════ ▲

Current Effect: `{current_effect}`
Effect Speed: `{current_speed}`
Watermark Type: `{watermark_type}`

▼ ═══════════════════════ ▼
Choose watermark effect for videos:

⚠️ **Note:** Effects only work on videos!

**TEXT Effects:**
• Scroll, Fade, Pulse, Wave

**IMAGE/GIF Effects:**  
• Diagonal movement (all types!)
• Rotation (all types!)

**Speed:** Higher value = Slower movement
• 5-20: Fast
• 35-50: Medium  
• 70-100: Slow"""

    kb = [
        [InlineKeyboardButton("◽ None (Static)", callback_data=f"set_effect_none_{ch_id}")],
    ]

    # Text-only effects
    if watermark_type == "text":
        kb.extend([
            [InlineKeyboardButton("⬅️ Scroll Left", callback_data=f"set_effect_scroll_left_{ch_id}")],
            [InlineKeyboardButton("➡️ Scroll Right", callback_data=f"set_effect_scroll_right_{ch_id}")],
            [InlineKeyboardButton("⬆️ Scroll Up", callback_data=f"set_effect_scroll_up_{ch_id}")],
            [InlineKeyboardButton("⬇️ Scroll Down", callback_data=f"set_effect_scroll_down_{ch_id}")],
            [InlineKeyboardButton("🌫️ Fade In/Out", callback_data=f"set_effect_fade_{ch_id}")],
            [InlineKeyboardButton("💫 Pulse/Glow", callback_data=f"set_effect_pulse_{ch_id}")],
            [InlineKeyboardButton("🌊 Smooth Wave", callback_data=f"set_effect_wave_{ch_id}")],
        ])

    # Moving diagonal effects (works for all types!)
    kb.extend([
        [InlineKeyboardButton("↘️ Move: Top-Left → Down-Right", callback_data=f"set_effect_move_diagonal_dr_{ch_id}")],
        [InlineKeyboardButton("↙️ Move: Top-Right → Down-Left", callback_data=f"set_effect_move_diagonal_dl_{ch_id}")],
        [InlineKeyboardButton("↗️ Move: Bottom-Left → Up-Right", callback_data=f"set_effect_move_diagonal_ur_{ch_id}")],
        [InlineKeyboardButton("↖️ Move: Bottom-Right → Up-Left", callback_data=f"set_effect_move_diagonal_ul_{ch_id}")],
        [InlineKeyboardButton("⚡ Set Speed", callback_data=f"set_effect_speed_{ch_id}")],
        [InlineKeyboardButton("⪡ Back to Watermark Settings", callback_data=f"channel_settings_auto_watermark_{ch_id}")]
    ])

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def process_media_group(context: ContextTypes.DEFAULT_TYPE):
    """Process a complete media group (album) with watermarks using editMessageMedia."""
    job = context.job
    media_group_id = job.data['media_group_id']

    if 'media_groups' not in context.bot_data:
        logger.warning(f"media_groups not found in bot_data")
        return

    if media_group_id not in context.bot_data['media_groups']:
        logger.warning(f"Album {media_group_id} not found in media_groups")
        return

    album_data = context.bot_data['media_groups'][media_group_id]

    # Check if already processed
    if album_data.get('processed', False):
        logger.info(f"Album {media_group_id} already processed, skipping")
        return

    # Mark as processed immediately to prevent duplicate execution
    album_data['processed'] = True

    messages = album_data['messages']
    ch_id = album_data['chat_id']

    logger.info(f"Processing album {media_group_id} with {len(messages)} messages")

    # Find channel config
    user_id = None
    channel_config = None
    for uid, channels in USER_CHANNELS.items():
        for ch in channels:
            if ch['id'] == ch_id:
                user_id = uid
                channel_config = ch
                break
        if user_id:
            break

    if not channel_config:
        logger.warning(f"No config found for album in channel {ch_id}")
        del context.bot_data['media_groups'][media_group_id]
        return

    # Check if watermark is active
    auto_watermark_settings = channel_config.get("auto_watermark")
    watermark_active = (auto_watermark_settings and 
                       auto_watermark_settings.get("status") == "active" and
                       auto_watermark_settings.get("config") not in [None, "Not set"])

    if not watermark_active:
        logger.info(f"Watermark not active for album, skipping")
        del context.bot_data['media_groups'][media_group_id]
        return

    # Process each message in the album using editMessageMedia
    success_count = 0
    for msg in messages:
        try:
            # Get watermark settings
            watermark_config_text = auto_watermark_settings.get("config")
            watermark_position = auto_watermark_settings.get("position", "bottom_right")
            watermark_size = auto_watermark_settings.get("size", 50)
            watermark_transparency = auto_watermark_settings.get("transparency", 50)
            watermark_quality = auto_watermark_settings.get("quality", 75)
            watermark_rotation = auto_watermark_settings.get("rotation", 0)
            watermark_color = auto_watermark_settings.get("color", "white")
            watermark_effect = auto_watermark_settings.get("effect", "none")
            watermark_effect_speed = auto_watermark_settings.get("effect_speed", 50)

            # Get caption
            caption = msg.caption if msg.caption else None
            caption_entities = msg.caption_entities if msg.caption_entities else None

            # Download and watermark
            if msg.photo:
                file_id = msg.photo[-1].file_id
                downloaded_path = await download_telegram_file(context, file_id, "jpg", max_size_mb=20)
                watermarked_path = TEMP_DIR / f"watermarked_{uuid.uuid4()}.jpg"

                await apply_watermark(
                    downloaded_path, watermarked_path, watermark_config_text,
                    watermark_position, watermark_size, watermark_transparency,
                    watermark_quality, is_video=False,
                    rotation=watermark_rotation, color=watermark_color,
                    effect=watermark_effect, effect_speed=watermark_effect_speed
                )

                # Replace media using editMessageMedia
                with open(watermarked_path, 'rb') as f:
                    media = InputMediaPhoto(media=f, caption=caption, caption_entities=caption_entities)

                    try:
                        await context.bot.edit_message_media(
                            chat_id=ch_id,
                            message_id=msg.message_id,
                            media=media
                        )
                        logger.info(f"Replaced photo {msg.message_id} in album using editMessageMedia")
                        success_count += 1
                    except TelegramError as e:
                        logger.error(f"Error replacing photo {msg.message_id} in album: {e}")

                # Cleanup
                downloaded_path.unlink()
                watermarked_path.unlink()

            elif msg.video:
                file_id = msg.video.file_id
                downloaded_path = await download_telegram_file(context, file_id, "mp4", max_size_mb=20)
                watermarked_path = TEMP_DIR / f"watermarked_{uuid.uuid4()}.mp4"

                await apply_watermark(
                    downloaded_path, watermarked_path, watermark_config_text,
                    watermark_position, watermark_size, watermark_transparency,
                    watermark_quality, is_video=True,
                    rotation=watermark_rotation, color=watermark_color,
                    effect=watermark_effect, effect_speed=watermark_effect_speed
                )

                # Replace media using editMessageMedia
                with open(watermarked_path, 'rb') as f:
                    media = InputMediaVideo(media=f, caption=caption, caption_entities=caption_entities)

                    try:
                        await context.bot.edit_message_media(
                            chat_id=ch_id,
                            message_id=msg.message_id,
                            media=media
                        )
                        logger.info(f"Replaced video {msg.message_id} in album using editMessageMedia")
                        success_count += 1
                    except TelegramError as e:
                        logger.error(f"Error replacing video {msg.message_id} in album: {e}")

                # Cleanup
                downloaded_path.unlink()
                watermarked_path.unlink()

        except Exception as e:
            logger.error(f"Error watermarking message {msg.message_id} in album: {e}")
            # Continue with other messages
            continue

    logger.info(f"Album processing complete: {success_count}/{len(messages)} items watermarked")

    # Cleanup
    del context.bot_data['media_groups'][media_group_id]


async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages, photos, and animations from users for configuration."""
    user_id = update.effective_user.id
    message = update.message
    text = message.text if message.text else None
    next_step = context.user_data.get('next_step')

    # Debug logging
    logger.info(f"handle_user_message: user={user_id}, text={bool(text)}, photo={bool(message.photo)}, animation={bool(message.animation)}, next_step={next_step}")

    if next_step and next_step.startswith('set_auto_button_config_'):
        ch_id = int(next_step.split("_")[-1])

        # Button config requires text
        if not text:
            await update.message.reply_text("❌ Please send text for button configuration.")
            return

        channels = USER_CHANNELS.get(user_id, [])
        chosen = next((c for c in channels if c["id"] == ch_id), None)
        if chosen:
            chosen.setdefault("auto_button", {})["config"] = text
            save_data()

            await update.message.reply_text("✅ Button configuration updated!")
            del context.user_data['next_step']

            await send_auto_button_settings(update, context, user_id, ch_id)
        else:
            await update.message.reply_text("Channel not found.")
            del context.user_data['next_step']
        return

    if next_step and next_step.startswith('set_auto_watermark_config_'):
        ch_id = int(next_step.split("_")[-1])
        logger.info(f"[WATERMARK] Processing watermark config for channel {ch_id}")
        logger.info(f"[WATERMARK] Message has: text={bool(message.text)}, photo={bool(message.photo)}, animation={bool(message.animation)}")

        channels = USER_CHANNELS.get(user_id, [])
        chosen = next((c for c in channels if c["id"] == ch_id), None)
        if chosen:
            watermark_data = chosen.setdefault("auto_watermark", {})
            logger.info(f"[WATERMARK] Current watermark_data: {watermark_data}")

            # Check if user sent text, photo, or animation
            if message.text:
                # Text watermark
                logger.info(f"[WATERMARK] Saving text watermark: {text}")
                watermark_data["type"] = "text"
                watermark_data["config"] = text
                # Clear file data if switching from image/gif
                watermark_data.pop("file_id", None)
                watermark_data.pop("file_path", None)
                await update.message.reply_text("✅ Text watermark saved!")

            elif message.photo:
                # Image watermark (download and save file_id)
                file_id = message.photo[-1].file_id
                logger.info(f"[WATERMARK] Saving image watermark, file_id: {file_id}")
                watermark_data["type"] = "image"
                watermark_data["config"] = "Image watermark"
                watermark_data["file_id"] = file_id

                # Download and save the image file
                try:
                    watermark_file = await context.bot.get_file(file_id)
                    watermark_path = TEMP_DIR / f"watermark_{ch_id}.png"
                    logger.info(f"[WATERMARK] Downloading watermark to: {watermark_path}")
                    await watermark_file.download_to_drive(watermark_path)
                    watermark_data["file_path"] = str(watermark_path)
                    logger.info(f"[WATERMARK] ✓ Saved image watermark: {watermark_path} (exists: {watermark_path.exists()}, size: {watermark_path.stat().st_size if watermark_path.exists() else 0} bytes)")
                except Exception as e:
                    logger.error(f"[WATERMARK] ✗ Error saving image watermark: {e}", exc_info=True)
                    await update.message.reply_text(f"⚠️ Image saved but download failed: {e}\nTry sending again.")
                    return

                await update.message.reply_text("✅ Image watermark saved! Your logo will be overlaid on posts.")

            elif message.animation:
                # GIF/Animation watermark
                file_id = message.animation.file_id
                logger.info(f"[WATERMARK] Saving GIF watermark, file_id: {file_id}")
                watermark_data["type"] = "animation"
                watermark_data["config"] = "GIF watermark"
                watermark_data["file_id"] = file_id

                # Download and save the animation file
                try:
                    watermark_file = await context.bot.get_file(file_id)
                    watermark_path = TEMP_DIR / f"watermark_{ch_id}.gif"
                    logger.info(f"[WATERMARK] Downloading watermark to: {watermark_path}")
                    await watermark_file.download_to_drive(watermark_path)
                    watermark_data["file_path"] = str(watermark_path)
                    logger.info(f"[WATERMARK] ✓ Saved GIF watermark: {watermark_path} (exists: {watermark_path.exists()}, size: {watermark_path.stat().st_size if watermark_path.exists() else 0} bytes)")
                except Exception as e:
                    logger.error(f"[WATERMARK] ✗ Error saving GIF watermark: {e}", exc_info=True)
                    await update.message.reply_text(f"⚠️ GIF saved but download failed: {e}\nTry sending again.")
                    return

                await update.message.reply_text("✅ GIF watermark saved! Your animated logo will be overlaid on posts.")
            else:
                logger.warning("[WATERMARK] Unknown message type for watermark config")
                await update.message.reply_text("❌ Please send text, image, or GIF as watermark.")
                return

            logger.info(f"[WATERMARK] Final watermark_data: type={watermark_data.get('type')}, config={watermark_data.get('config')}, file_id={watermark_data.get('file_id')[:20] if watermark_data.get('file_id') else None}...")
            save_data()
            logger.info(f"[WATERMARK] ✓ Data saved to disk")
            del context.user_data['next_step']
            await send_auto_watermark_settings(update, context, user_id, ch_id)
        else:
            logger.error(f"Channel {ch_id} not found for user {user_id}")
            await update.message.reply_text("Channel not found.")
            del context.user_data['next_step']
        return

    if next_step and next_step.startswith('set_watermark_size_value_'):
        ch_id = int(next_step.split("_")[-1])

        channels = USER_CHANNELS.get(user_id, [])
        chosen = next((c for c in channels if c["id"] == ch_id), None)
        if chosen:
            try:
                size = int(text)
                if 1 <= size <= 100:
                    chosen.setdefault("auto_watermark", {})["size"] = size
                    save_data()
                    await update.message.reply_text("✅ Watermark size updated!")
                else:
                    await update.message.reply_text("❌ Size must be between 1 and 100.")
            except ValueError:
                await update.message.reply_text("❌ Invalid input. Please send a number for the size.")

            del context.user_data['next_step']
            await send_auto_watermark_settings(update, context, user_id, ch_id)
        else:
            await update.message.reply_text("Channel not found.")
            del context.user_data['next_step']
        return

    if next_step and next_step.startswith('set_watermark_transparency_value_'):
        ch_id = int(next_step.split("_")[-1])

        channels = USER_CHANNELS.get(user_id, [])
        chosen = next((c for c in channels if c["id"] == ch_id), None)
        if chosen:
            try:
                transparency = int(text)
                if 0 <= transparency <= 95:
                    chosen.setdefault("auto_watermark", {})["transparency"] = transparency
                    save_data()
                    await update.message.reply_text("✅ Watermark transparency updated!")
                else:
                    await update.message.reply_text("❌ Transparency must be between 0 and 95.")
            except ValueError:
                await update.message.reply_text("❌ Invalid input. Please send a number for the transparency.")

            del context.user_data['next_step']
            await send_auto_watermark_settings(update, context, user_id, ch_id)
        else:
            await update.message.reply_text("Channel not found.")
            del context.user_data['next_step']
        return

    if next_step and next_step.startswith('set_watermark_quality_value_'):
        ch_id = int(next_step.split("_")[-1])

        channels = USER_CHANNELS.get(user_id, [])
        chosen = next((c for c in channels if c["id"] == ch_id), None)
        if chosen:
            try:
                quality = int(text)
                if 1 <= quality <= 100:
                    chosen.setdefault("auto_watermark", {})["quality"] = quality
                    save_data()
                    await update.message.reply_text("✅ Watermark quality updated!")
                else:
                    await update.message.reply_text("❌ Quality must be between 1 and 100.")
            except ValueError:
                await update.message.reply_text("❌ Invalid input. Please send a number for the quality.")

            del context.user_data['next_step']
            await send_auto_watermark_settings(update, context, user_id, ch_id)
        else:
            await update.message.reply_text("Channel not found.")
            del context.user_data['next_step']
        return

    if next_step and next_step.startswith('set_watermark_rotation_value_'):
        ch_id = int(next_step.split("_")[-1])

        channels = USER_CHANNELS.get(user_id, [])
        chosen = next((c for c in channels if c["id"] == ch_id), None)
        if chosen:
            try:
                rotation = int(text)
                if 0 <= rotation <= 360:
                    chosen.setdefault("auto_watermark", {})["rotation"] = rotation
                    save_data()
                    await update.message.reply_text(f"✅ Rotation set to {rotation}°!")
                else:
                    await update.message.reply_text("❌ Rotation must be between 0 and 360 degrees.")
            except ValueError:
                await update.message.reply_text("❌ Invalid input. Please send a number for the rotation.")

            del context.user_data['next_step']
            await send_auto_watermark_settings(update, context, user_id, ch_id)
        else:
            await update.message.reply_text("Channel not found.")
            del context.user_data['next_step']
        return

    if next_step and next_step.startswith('set_effect_speed_value_'):
        ch_id = int(next_step.split("_")[-1])

        channels = USER_CHANNELS.get(user_id, [])
        chosen = next((c for c in channels if c["id"] == ch_id), None)
        if chosen:
            try:
                speed = int(text)
                if 1 <= speed <= 100:
                    chosen.setdefault("auto_watermark", {})["effect_speed"] = speed
                    save_data()
                    speed_desc = "Very Fast" if speed <= 10 else "Fast" if speed <= 20 else "Medium" if speed <= 50 else "Slow" if speed <= 70 else "Very Slow"
                    await update.message.reply_text(f"✅ Effect speed set to {speed} ({speed_desc})!")
                else:
                    await update.message.reply_text("❌ Speed must be between 1 and 100.")
            except ValueError:
                await update.message.reply_text("❌ Invalid input. Please send a number for the speed.")

            del context.user_data['next_step']
            await send_auto_watermark_settings(update, context, user_id, ch_id)
        else:
            await update.message.reply_text("Channel not found.")
            del context.user_data['next_step']
        return

    if next_step and next_step.startswith('set_auto_caption_config_'):
        ch_id = int(next_step.split("_")[-1])

        channels = USER_CHANNELS.get(user_id, [])
        chosen = next((c for c in channels if c["id"] == ch_id), None)
        if chosen:
            auto_captions = chosen.setdefault("auto_captions", {})
            auto_captions["config"] = message.text
            auto_captions["entities"] = [e.to_dict() for e in message.entities] if message.entities else []
            save_data()

            await update.message.reply_text("✅ Caption configuration updated!")
            del context.user_data['next_step']

            await send_auto_caption_settings(update, context, user_id, ch_id)
        else:
            await update.message.reply_text("Channel not found.")
            del context.user_data['next_step']
        return


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channel_post = update.channel_post
    if not channel_post:
        return

    ch_id = channel_post.chat_id

    # Check if this is part of a media group (album)
    media_group_id = channel_post.media_group_id
    is_album = media_group_id is not None

    # If it's part of an album, store it for batch processing
    if is_album:
        if 'media_groups' not in context.bot_data:
            context.bot_data['media_groups'] = {}

        if media_group_id not in context.bot_data['media_groups']:
            context.bot_data['media_groups'][media_group_id] = {
                'messages': [],
                'chat_id': ch_id,
                'timestamp': channel_post.date.timestamp(),
                'job_scheduled': False  # Track if job is already scheduled
            }

        context.bot_data['media_groups'][media_group_id]['messages'].append(channel_post)
        logger.info(f"Stored album message {channel_post.message_id} in group {media_group_id}")

        # Schedule album processing only ONCE per album
        if not context.bot_data['media_groups'][media_group_id]['job_scheduled']:
            context.job_queue.run_once(
                process_media_group,
                when=2.0,
                data={'media_group_id': media_group_id},
                name=f"album_{media_group_id}"
            )
            context.bot_data['media_groups'][media_group_id]['job_scheduled'] = True
            logger.info(f"Scheduled album processing job for {media_group_id}")

        return  # Don't process individual messages from album yet

    user_id = None
    channel_config = None
    for uid, channels in USER_CHANNELS.items():
        for ch in channels:
            if ch['id'] == ch_id:
                user_id = uid
                channel_config = ch
                break
        if user_id:
            break

    if not channel_config:
        return

    # --- Check if watermark is active first ---
    auto_watermark_settings = channel_config.get("auto_watermark")
    watermark_active = (auto_watermark_settings and 
                       auto_watermark_settings.get("status") == "active" and
                       auto_watermark_settings.get("config") not in [None, "Not set"])

    # --- If watermark is active and media is present, handle it separately ---
    if watermark_active and (channel_post.photo or channel_post.video or channel_post.animation):
        watermark_type = auto_watermark_settings.get("type", "text")
        watermark_config_text = auto_watermark_settings.get("config")
        watermark_position = auto_watermark_settings.get("position", "bottom_right")
        watermark_size = auto_watermark_settings.get("size", 50)
        watermark_transparency = auto_watermark_settings.get("transparency", 50)
        watermark_quality = auto_watermark_settings.get("quality", 75)
        watermark_rotation = auto_watermark_settings.get("rotation", 0)
        watermark_color = auto_watermark_settings.get("color", "white")
        watermark_effect = auto_watermark_settings.get("effect", "none")
        watermark_effect_speed = auto_watermark_settings.get("effect_speed", 50)
        watermark_file_id = auto_watermark_settings.get("file_id")
        watermark_file_path = auto_watermark_settings.get("file_path")

        logger.info(f"Watermark active: type={watermark_type}, config={watermark_config_text}, file_id={watermark_file_id}, file_path={watermark_file_path}")

        # Validate settings based on type
        if watermark_type == "text":
            if not watermark_config_text or watermark_config_text == "Not set":
                logger.warning(f"Text watermark active but no text configured for channel {ch_id}")
                return
        elif watermark_type in ["image", "animation"]:
            if not watermark_file_id and not watermark_file_path:
                logger.warning(f"Image watermark active but no file configured for channel {ch_id}")
                return

        # Prepare caption and entities from original message
        is_text_message = channel_post.text is not None
        original_text = channel_post.text if is_text_message else channel_post.caption
        original_entities = channel_post.entities if is_text_message else channel_post.caption_entities

        final_text = original_text
        final_entities = list(original_entities) if original_entities else []
        final_reply_markup = channel_post.reply_markup

        # --- Apply Auto Buttons ---
        auto_button_settings = channel_config.get("auto_button")
        if auto_button_settings and auto_button_settings.get("status") == "active":
            button_config_str = auto_button_settings.get("config")
            if button_config_str and button_config_str != "Not set":
                buttons = []
                lines = button_config_str.split('\n')
                for line in lines:
                    line_buttons = []
                    parts = line.split('&&')
                    for part in parts:
                        part = part.strip()
                        if ' - ' in part and ' - popup:' not in part:
                            text, url = part.split(' - ', 1)
                            text = text.strip()
                            url = url.strip()
                            if not url.startswith(('http', 't.me')):
                                url = 'http://' + url
                            line_buttons.append(InlineKeyboardButton(text, url=url))
                    if line_buttons:
                        buttons.append(line_buttons)

                if buttons:
                    final_reply_markup = InlineKeyboardMarkup(buttons)

        # --- Apply Auto Captions ---
        auto_caption_settings = channel_config.get("auto_captions")
        if auto_caption_settings and auto_caption_settings.get("status") == "active":
            caption_config_text = auto_caption_settings.get("config")
            caption_config_entities_dict = auto_caption_settings.get("entities", [])

            if caption_config_text and caption_config_text != "Not set":
                caption_config_entities = [MessageEntity(**e) for e in caption_config_entities_dict]

                if final_text is not None:
                    separator = "\n\n"
                    base_len = len(final_text.encode('utf-16-le')) // 2
                    final_text += separator + caption_config_text

                    offset = base_len + len(separator.encode('utf-16-le')) // 2
                    # Create new entities with adjusted offset (can't modify immutable entities)
                    adjusted_entities = [
                        MessageEntity(
                            type=entity.type,
                            offset=entity.offset + offset,
                            length=entity.length,
                            url=entity.url,
                            user=entity.user,
                            language=entity.language,
                            custom_emoji_id=entity.custom_emoji_id
                        ) for entity in caption_config_entities
                    ]
                    final_entities.extend(adjusted_entities)
                else:
                    final_text = caption_config_text
                    final_entities = caption_config_entities

        # --- Apply Watermark to Media ---
        downloaded_file_path = None
        watermarked_file_path = None
        try:
            if channel_post.photo:
                logger.info(f"Applying {watermark_type} watermark to photo for channel {ch_id}")

                file_id = channel_post.photo[-1].file_id
                downloaded_file_path = await download_telegram_file(context, file_id, "jpg")

                watermarked_file_path = TEMP_DIR / f"watermarked_{uuid.uuid4()}.jpg"

                logger.info(f"[WATERMARK] Watermark type: {watermark_type}")
                logger.info(f"[WATERMARK] file_id: {watermark_file_id[:20] if watermark_file_id else None}...")
                logger.info(f"[WATERMARK] file_path: {watermark_file_path}")

                # Apply appropriate watermark type
                if watermark_type in ["image", "animation"]:
                    # Image/GIF watermark overlay
                    logger.info(f"[WATERMARK] Applying image/GIF watermark overlay")
                    watermark_image_path = Path(watermark_file_path) if watermark_file_path else None
                    logger.info(f"[WATERMARK] Watermark path: {watermark_image_path}")
                    logger.info(f"[WATERMARK] Path exists: {watermark_image_path.exists() if watermark_image_path else False}")

                    if not watermark_image_path or not watermark_image_path.exists():
                        # Re-download if file doesn't exist
                        logger.info(f"[WATERMARK] Watermark file missing, re-downloading for channel {ch_id}")
                        if not watermark_file_id:
                            raise Exception("No watermark file_id found. Please set watermark again.")

                        watermark_file = await context.bot.get_file(watermark_file_id)
                        watermark_image_path = TEMP_DIR / f"watermark_{ch_id}_temp.png"
                        await watermark_file.download_to_drive(watermark_image_path)
                        logger.info(f"[WATERMARK] Re-downloaded to: {watermark_image_path} (exists: {watermark_image_path.exists()})")

                    logger.info(f"[WATERMARK] Calling apply_image_watermark with size={watermark_size}%, transparency={watermark_transparency}%, rotation={watermark_rotation}°")
                    await apply_image_watermark(
                        downloaded_file_path,
                        watermarked_file_path,
                        watermark_image_path,
                        watermark_position,
                        watermark_size,
                        watermark_transparency,
                        watermark_quality,
                        is_video=False,
                        rotation=watermark_rotation,
                        effect=watermark_effect,
                        effect_speed=watermark_effect_speed,
                    )
                    logger.info(f"[WATERMARK] ✓ Image watermark applied successfully")
                else:
                    # Text watermark
                    logger.info(f"[WATERMARK] Applying text watermark: {watermark_config_text}")
                    await apply_watermark(
                        downloaded_file_path,
                        watermarked_file_path,
                        watermark_config_text,
                        watermark_position,
                        watermark_size,
                        watermark_transparency,
                        watermark_quality,
                        is_video=False,
                        rotation=watermark_rotation,
                        color=watermark_color,
                        effect=watermark_effect,
                        effect_speed=watermark_effect_speed,
                    )
                    logger.info(f"[WATERMARK] ✓ Text watermark applied successfully")

                # Replace media using editMessageMedia (preserves message ID, reactions, etc.)
                with open(watermarked_file_path, 'rb') as f:
                    media = InputMediaPhoto(
                        media=f,
                        caption=final_text,
                        caption_entities=final_entities,
                    )

                    try:
                        edited_message = await context.bot.edit_message_media(
                            chat_id=ch_id,
                            message_id=channel_post.message_id,
                            media=media,
                            reply_markup=final_reply_markup,
                        )
                        logger.info(f"Replaced photo with watermarked version using editMessageMedia")
                    except TelegramError as e:
                        logger.error(f"Error replacing media: {e}")
                        # Fallback to delete+send if edit fails
                        logger.info("Falling back to delete+send method")
                        try:
                            await context.bot.delete_message(chat_id=ch_id, message_id=channel_post.message_id)
                        except TelegramError:
                            pass

                        with open(watermarked_file_path, 'rb') as f2:
                            edited_message = await context.bot.send_photo(
                                chat_id=ch_id,
                                photo=f2,
                                caption=final_text,
                                caption_entities=final_entities,
                                reply_markup=final_reply_markup,
                            )

                # Handle Auto Reactions on the message
                auto_reactions_settings = channel_config.get("auto_reactions")
                if auto_reactions_settings and auto_reactions_settings.get("status") == "active":
                    try:
                        # Add small delay to ensure message is fully processed
                        await asyncio.sleep(0.5)
                        reactions = ["👍", "❤️", "🔥"]
                        await context.bot.set_message_reaction(
                            chat_id=ch_id,
                            message_id=edited_message.message_id,
                            reaction=[ReactionTypeEmoji(emoji=reactions[0])]
                        )
                        logger.info(f"Applied reaction to watermarked photo")
                    except TelegramError as e:
                        # Ignore reaction errors (message might not support reactions)
                        logger.warning(f"Could not set reaction (this is normal): {e}")

                return  # Exit handler - we've replaced the message

            elif channel_post.video:
                logger.info(f"Applying {watermark_type} watermark to video for channel {ch_id}")

                file_id = channel_post.video.file_id
                downloaded_file_path = await download_telegram_file(context, file_id, "mp4")

                watermarked_file_path = TEMP_DIR / f"watermarked_{uuid.uuid4()}.mp4"

                logger.info(f"[WATERMARK] Watermark type for video: {watermark_type}")

                # Apply appropriate watermark type
                if watermark_type in ["image", "animation"]:
                    # Image/GIF watermark overlay
                    logger.info(f"[WATERMARK] Applying image/GIF watermark overlay to video")
                    watermark_image_path = Path(watermark_file_path) if watermark_file_path else None

                    if not watermark_image_path or not watermark_image_path.exists():
                        # Re-download if file doesn't exist
                        logger.info(f"[WATERMARK] Watermark file missing, re-downloading for channel {ch_id}")
                        if not watermark_file_id:
                            raise Exception("No watermark file_id found. Please set watermark again.")

                        watermark_file = await context.bot.get_file(watermark_file_id)
                        watermark_image_path = TEMP_DIR / f"watermark_{ch_id}_temp.png"
                        await watermark_file.download_to_drive(watermark_image_path)
                        logger.info(f"[WATERMARK] Re-downloaded to: {watermark_image_path}")

                    logger.info(f"[WATERMARK] Calling apply_image_watermark for video with rotation={watermark_rotation}°, effect={watermark_effect}")
                    await apply_image_watermark(
                        downloaded_file_path,
                        watermarked_file_path,
                        watermark_image_path,
                        watermark_position,
                        watermark_size,
                        watermark_transparency,
                        watermark_quality,
                        is_video=True,
                        rotation=watermark_rotation,
                        effect=watermark_effect,
                        effect_speed=watermark_effect_speed,
                    )
                    logger.info(f"[WATERMARK] ✓ Image watermark applied to video")
                else:
                    # Text watermark
                    logger.info(f"[WATERMARK] Applying text watermark to video")
                    await apply_watermark(
                        downloaded_file_path,
                        watermarked_file_path,
                        watermark_config_text,
                        watermark_position,
                        watermark_size,
                        watermark_transparency,
                        watermark_quality,
                        is_video=True,
                        rotation=watermark_rotation,
                        color=watermark_color,
                        effect=watermark_effect,
                        effect_speed=watermark_effect_speed,
                    )
                    logger.info(f"[WATERMARK] ✓ Text watermark applied to video")

                # Replace media using editMessageMedia (preserves message ID, reactions, etc.)
                with open(watermarked_file_path, 'rb') as f:
                    media = InputMediaVideo(
                        media=f,
                        caption=final_text,
                        caption_entities=final_entities,
                    )

                    try:
                        edited_message = await context.bot.edit_message_media(
                            chat_id=ch_id,
                            message_id=channel_post.message_id,
                            media=media,
                            reply_markup=final_reply_markup,
                        )
                        logger.info(f"Replaced video with watermarked version using editMessageMedia")
                    except TelegramError as e:
                        logger.error(f"Error replacing media: {e}")
                        # Fallback to delete+send if edit fails
                        logger.info("Falling back to delete+send method")
                        try:
                            await context.bot.delete_message(chat_id=ch_id, message_id=channel_post.message_id)
                        except TelegramError:
                            pass

                        with open(watermarked_file_path, 'rb') as f2:
                            edited_message = await context.bot.send_video(
                                chat_id=ch_id,
                                video=f2,
                                caption=final_text,
                                caption_entities=final_entities,
                                reply_markup=final_reply_markup,
                            )

                # Handle Auto Reactions on the message
                auto_reactions_settings = channel_config.get("auto_reactions")
                if auto_reactions_settings and auto_reactions_settings.get("status") == "active":
                    try:
                        # Add small delay to ensure message is fully processed
                        await asyncio.sleep(0.5)
                        reactions = ["👍", "❤️", "🔥"]
                        await context.bot.set_message_reaction(
                            chat_id=ch_id,
                            message_id=edited_message.message_id,
                            reaction=[ReactionTypeEmoji(emoji=reactions[0])]
                        )
                        logger.info(f"Applied reaction to watermarked video")
                    except TelegramError as e:
                        # Ignore reaction errors (message might not support reactions)
                        logger.warning(f"Could not set reaction (this is normal): {e}")

                return  # Exit handler

            elif channel_post.animation:
                logger.info(f"Applying GIF watermark for channel {ch_id}")

                file_id = channel_post.animation.file_id
                downloaded_file_path = await download_telegram_file(context, file_id, "mp4")

                watermarked_file_path = TEMP_DIR / f"watermarked_{uuid.uuid4()}.mp4"
                await apply_watermark(
                    downloaded_file_path,
                    watermarked_file_path,
                    watermark_config_text,
                    watermark_position,
                    watermark_size,
                    watermark_transparency,
                    watermark_quality,
                    is_video=True,
                    rotation=watermark_rotation,
                    color=watermark_color,
                    effect=watermark_effect,
                    effect_speed=watermark_effect_speed,
                )

                # Replace media using editMessageMedia (preserves message ID, reactions, etc.)
                with open(watermarked_file_path, 'rb') as f:
                    media = InputMediaAnimation(
                        media=f,
                        caption=final_text,
                        caption_entities=final_entities,
                    )

                    try:
                        edited_message = await context.bot.edit_message_media(
                            chat_id=ch_id,
                            message_id=channel_post.message_id,
                            media=media,
                            reply_markup=final_reply_markup,
                        )
                        logger.info(f"Replaced animation with watermarked version using editMessageMedia")
                    except TelegramError as e:
                        logger.error(f"Error replacing media: {e}")
                        # Fallback to delete+send if edit fails
                        logger.info("Falling back to delete+send method")
                        try:
                            await context.bot.delete_message(chat_id=ch_id, message_id=channel_post.message_id)
                        except TelegramError:
                            pass

                        with open(watermarked_file_path, 'rb') as f2:
                            edited_message = await context.bot.send_animation(
                                chat_id=ch_id,
                                animation=f2,
                                caption=final_text,
                                caption_entities=final_entities,
                                reply_markup=final_reply_markup,
                            )

                # Handle Auto Reactions on the message
                auto_reactions_settings = channel_config.get("auto_reactions")
                if auto_reactions_settings and auto_reactions_settings.get("status") == "active":
                    try:
                        # Add small delay to ensure message is fully sent
                        await asyncio.sleep(0.5)
                        reactions = ["👍", "❤️", "🔥"]
                        await context.bot.set_message_reaction(
                            chat_id=ch_id,
                            message_id=edited_message.message_id,
                            reaction=[ReactionTypeEmoji(emoji=reactions[0])]
                        )
                        logger.info(f"Applied reaction to watermarked animation")
                    except TelegramError as e:
                        # Ignore reaction errors (message might not support reactions)
                        logger.warning(f"Could not set reaction (this is normal): {e}")

                return  # Exit handler

        except Exception as e:
            logger.error(f"Error during watermarking process: {e}")
            # Send detailed error notification to channel
            error_type = type(e).__name__
            error_msg = str(e)

            # Check if it's a file size error
            if "too big" in error_msg.lower() or "file is too large" in error_msg.lower():
                error_details = f"⚠️ **Watermarking Failed: File Too Large**\n\n"
                error_details += f"The file is too large to process. Telegram bot API limit is 20MB for downloads.\n\n"
                error_details += f"📍 **Message Details:**\n"
                error_details += f"• Chat ID: `{ch_id}`\n"
                error_details += f"• Message ID: `{channel_post.message_id}`\n"

                # Create message link
                channel_username = None
                for ch in USER_CHANNELS.get(context.bot_data.get('MAIN_ADMIN_ID', 0), []):
                    if ch.get('id') == ch_id:
                        channel_username = ch.get('username')
                        break

                if channel_username:
                    msg_link = f"https://t.me/{channel_username}/{channel_post.message_id}"
                    error_details += f"• Link: {msg_link}\n"

                error_details += f"\n💡 **Tip:** Upload smaller files or use compression."
            else:
                error_details = f"⚠️ **Watermarking Failed**\n\n"
                error_details += f"**Error:** {error_type}\n"
                error_details += f"**Details:** {error_msg[:200]}\n\n"
                error_details += f"📍 **Message Details:**\n"
                error_details += f"• Chat ID: `{ch_id}`\n"
                error_details += f"• Message ID: `{channel_post.message_id}`\n"

                # Create message link
                channel_username = None
                for ch in USER_CHANNELS.get(context.bot_data.get('MAIN_ADMIN_ID', 0), []):
                    if ch.get('id') == ch_id:
                        channel_username = ch.get('username')
                        break

                if channel_username:
                    msg_link = f"https://t.me/{channel_username}/{channel_post.message_id}"
                    error_details += f"• Link: {msg_link}\n"

                error_details += f"\n💡 Check logs for full error details."

            try:
                await context.bot.send_message(
                    chat_id=ch_id,
                    text=error_details,
                    parse_mode="Markdown"
                )
            except TelegramError as send_error:
                # If markdown fails, try plain text
                try:
                    await context.bot.send_message(
                        chat_id=ch_id,
                        text=error_details.replace("**", "").replace("`", ""),
                    )
                except TelegramError:
                    logger.error(f"Could not send error message: {send_error}")
        finally:
            # Clean up temporary files
            if downloaded_file_path and downloaded_file_path.exists():
                try:
                    downloaded_file_path.unlink()
                    logger.info(f"Cleaned up {downloaded_file_path}")
                except Exception as e:
                    logger.error(f"Error cleaning up {downloaded_file_path}: {e}")
            if watermarked_file_path and watermarked_file_path.exists():
                try:
                    watermarked_file_path.unlink()
                    logger.info(f"Cleaned up {watermarked_file_path}")
                except Exception as e:
                    logger.error(f"Error cleaning up {watermarked_file_path}: {e}")

    # --- If we reach here, either watermark is inactive or it's a text/sticker/other message ---
    # --- Handle normal editing for buttons, captions, reactions ---

    final_reply_markup = channel_post.reply_markup

    is_text_message = channel_post.text is not None
    original_text = channel_post.text if is_text_message else channel_post.caption
    original_entities = channel_post.entities if is_text_message else channel_post.caption_entities

    final_text = original_text
    final_entities = list(original_entities) if original_entities else []

    # --- Handle Auto Buttons ---
    auto_button_settings = channel_config.get("auto_button")
    if auto_button_settings and auto_button_settings.get("status") == "active":
        button_config_str = auto_button_settings.get("config")
        if button_config_str and button_config_str != "Not set":
            buttons = []
            lines = button_config_str.split('\n')
            for line in lines:
                line_buttons = []
                parts = line.split('&&')
                for part in parts:
                    part = part.strip()
                    if ' - ' in part and ' - popup:' not in part:
                        text, url = part.split(' - ', 1)
                        text = text.strip()
                        url = url.strip()
                        if not url.startswith(('http', 't.me')):
                            url = 'http://' + url
                        line_buttons.append(InlineKeyboardButton(text, url=url))
                if line_buttons:
                    buttons.append(line_buttons)

            if buttons:
                final_reply_markup = InlineKeyboardMarkup(buttons)

    # --- Handle Auto Captions ---
    auto_caption_settings = channel_config.get("auto_captions")
    if auto_caption_settings and auto_caption_settings.get("status") == "active":
        caption_config_text = auto_caption_settings.get("config")
        caption_config_entities_dict = auto_caption_settings.get("entities", [])

        if caption_config_text and caption_config_text != "Not set":
            caption_config_entities = [MessageEntity(**e) for e in caption_config_entities_dict]

            if final_text is not None:
                separator = "\n\n"
                base_len = len(final_text.encode('utf-16-le')) // 2
                final_text += separator + caption_config_text

                offset = base_len + len(separator.encode('utf-16-le')) // 2
                # Create new entities with adjusted offset (can't modify immutable entities)
                adjusted_entities = [
                    MessageEntity(
                        type=entity.type,
                        offset=entity.offset + offset,
                        length=entity.length,
                        url=entity.url,
                        user=entity.user,
                        language=entity.language,
                        custom_emoji_id=entity.custom_emoji_id
                    ) for entity in caption_config_entities
                ]
                final_entities.extend(adjusted_entities)
            else:
                final_text = caption_config_text
                final_entities = caption_config_entities

    # --- Handle Auto Watermark for text messages (append to caption) ---
    if watermark_active and channel_post.text:
        watermark_config_text = auto_watermark_settings.get("config")
        if final_text is not None:
            final_text += f"\n\n🔖 {watermark_config_text}"
        else:
            final_text = f"🔖 {watermark_config_text}"

    # --- Edit the message if anything has changed ---
    try:
        original_entities_tuple = tuple(e.to_dict() for e in original_entities) if original_entities else ()
        final_entities_tuple = tuple(e.to_dict() for e in final_entities) if final_entities else ()

        caption_changed = (final_text != original_text) or (final_entities_tuple != original_entities_tuple)
        markup_changed = final_reply_markup != channel_post.reply_markup

        if caption_changed or markup_changed:
            if is_text_message:
                await context.bot.edit_message_text(
                    chat_id=ch_id,
                    message_id=channel_post.message_id,
                    text=final_text,
                    entities=final_entities,
                    reply_markup=final_reply_markup,
                    parse_mode=None
                )
            else:
                await context.bot.edit_message_caption(
                    chat_id=ch_id,
                    message_id=channel_post.message_id,
                    caption=final_text or "",
                    caption_entities=final_entities,
                    reply_markup=final_reply_markup,
                    parse_mode=None
                )
    except TelegramError as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Error editing channel post: {e}")

    # --- Handle Auto Reactions ---
    auto_reactions_settings = channel_config.get("auto_reactions")
    if auto_reactions_settings and auto_reactions_settings.get("status") == "active":
        reactions = ["👍", "❤️", "🔥"]
        try:
            await context.bot.set_message_reaction(
                chat_id=ch_id,
                message_id=channel_post.message_id,
                reaction=[ReactionTypeEmoji(emoji=reactions[0])]
            )
        except TelegramError as e:
            logger.error(f"Error setting message reaction: {e}")


async def handle_forwarded_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """This handler captures forwarded messages from channels and groups."""
    msg = update.message
    user_id = update.effective_user.id

    origin = msg.forward_origin

    try:
        if isinstance(origin, MessageOriginChannel):
            fchat: Chat = origin.chat

            # Determine chat type
            chat_type = "channel"  # Default
            if fchat.type in ["group", "supergroup"]:
                chat_type = fchat.type
            elif fchat.type == "channel":
                chat_type = "channel"

            ch_info = {
                "id": fchat.id, 
                "title": fchat.title, 
                "username": fchat.username,
                "type": chat_type,  # Store the type
                "auto_button": {"status": "inactive", "config": "Not set"}, 
                "auto_captions": {"status": "inactive", "config": "Not set"}, 
                "auto_reactions": {"status": "inactive"}, 
                "auto_watermark": {
                    "status": "inactive", 
                    "config": "Not set", 
                    "position": "bottom_right", 
                    "size": 50, 
                    "transparency": 50, 
                    "quality": 75,
                    "rotation": 0,  # NEW: 0-360 degrees
                    "color": "white",  # NEW: white, black, red, blue, green, yellow, custom
                    "effect": "none",  # NEW: none, scroll_left, scroll_right, fade, pulse
                    "effect_speed": 1  # NEW: 1-10 (1=slow, 10=fast)
                }
            }

            USER_CHANNELS.setdefault(user_id, [])

            if any(c["id"] == ch_info["id"] for c in USER_CHANNELS[user_id]):
                chat_icon = "📢" if chat_type == "channel" else "👥"
                await msg.reply_text(
                    f"{chat_icon} I already have *{ch_info.get('title') or ch_info.get('username')}* saved.", 
                    parse_mode="Markdown"
                )
            else:
                USER_CHANNELS[user_id].append(ch_info)
                save_data()

                chat_icon = "📢" if chat_type == "channel" else "👥"
                chat_type_name = "Channel" if chat_type == "channel" else "Group"

                await msg.reply_text(
                    f"✅ *{chat_type_name} Saved Successfully!*\n\n"
                    f"{chat_icon} *{ch_info.get('title') or ch_info.get('username') or ch_info['id']}*\n\n"
                    f"You can now manage its settings from your list.",
                    parse_mode="Markdown",
                    reply_markup=make_channel_list_keyboard(user_id),
                )
        else:
            await msg.reply_text(
                "❌ *Could not detect forwarded message*\n\n"
                "Please forward a message from the channel or group (not copy/paste).\n\n"
                "If you can't forward, send the channel's username like `@channelusername`.",
                parse_mode="Markdown",
            )
    except TelegramError as e:
        logger.error(f"Error handling forwarded message: {e}")
        await msg.reply_text(
            f"❌ *Error occurred:* {str(e)}\n\n"
            "Please try again or contact support.",
            parse_mode="Markdown"
        )


async def handle_text_channel_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """If user sends @channelusername we try to resolve it via get_chat."""
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if not text.startswith("@"):
        return

    try:
        chat = await context.bot.get_chat(text)
    except TelegramError as e:
        logger.warning("get_chat failed for %s: %s", text, e)
        try:
            await update.message.reply_text(
                "❌ *Could not find that channel/group*\n\n"
                "I couldn't find that channel or I don't have permission to access it.\n\n"
                "Please try:\n"
                "1. Forward a message from the channel/group instead\n"
                "2. Add me as an admin and try again",
                parse_mode="Markdown"
            )
        except TelegramError:
            logger.error("Failed to send error message to user")
        return

    if chat.type not in ["channel", "group", "supergroup"]:
        await update.message.reply_text(
            "❌ That username is not a channel or group.",
            parse_mode="Markdown"
        )
        return

    # Determine chat type
    chat_type = chat.type

    ch_info = {
        "id": chat.id, 
        "title": chat.title, 
        "username": chat.username,
        "type": chat_type,  # Store the type
        "auto_button": {"status": "inactive", "config": "Not set"}, 
        "auto_captions": {"status": "inactive", "config": "Not set"}, 
        "auto_reactions": {"status": "inactive"}, 
        "auto_watermark": {
            "status": "inactive", 
            "config": "Not set", 
            "position": "bottom_right", 
            "size": 50, 
            "transparency": 50, 
            "quality": 75,
            "rotation": 0,
            "color": "white",
            "effect": "none",
            "effect_speed": 1
        }
    }
    USER_CHANNELS.setdefault(user_id, [])

    try:
        if any(c["id"] == ch_info["id"] for c in USER_CHANNELS[user_id]):
            chat_icon = "📢" if chat_type == "channel" else "👥"
            await update.message.reply_text(
                f"{chat_icon} I already have *{ch_info.get('title') or ch_info.get('username')}* saved.", 
                parse_mode="Markdown"
            )
        else:
            USER_CHANNELS[user_id].append(ch_info)
            save_data()

            chat_icon = "📢" if chat_type == "channel" else "👥"
            chat_type_name = "Channel" if chat_type == "channel" else "Group"

            await update.message.reply_text(
                f"✅ *{chat_type_name} Saved Successfully!*\n\n"
                f"{chat_icon} *{ch_info.get('title') or ch_info.get('username') or ch_info['id']}*",
                parse_mode="Markdown",
                reply_markup=make_channel_list_keyboard(user_id),
            )
    except TelegramError as e:
        logger.error(f"Error saving channel: {e}")
        await update.message.reply_text(
            f"❌ *Error occurred:* {str(e)}\n\nPlease try again.",
            parse_mode="Markdown"
        )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        help_text = (
            "📚 *HELP & INSTRUCTIONS*\n\n"
            "🚀 *Getting Started:*\n"
            "Use /start to see the main menu\n\n"
            "➕ *Adding a Channel/Group:*\n"
            "1. Click 'Add Channel/Group'\n"
            "2. Choose channel or group\n"
            "3. Add me as admin\n"
            "4. Forward a message from it to me\n\n"
            "⚙️ *Features:*\n"
            "• Auto Buttons - Add clickable buttons\n"
            "• Auto Captions - Append text to posts\n"
            "• Auto Reactions - Add emoji reactions\n"
            "• Auto Watermark - Brand your media\n\n"
            "❓ *Need Help?*\n"
            "Contact support or report issues"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")
    except TelegramError as e:
        logger.error(f"Error sending help message: {e}")


async def dump_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to dump all in-memory user data."""
    user_id = update.effective_user.id
    main_admin_id = context.bot_data.get('MAIN_ADMIN_ID')

    if user_id != main_admin_id:
        try:
            await update.message.reply_text("⛔ You are not authorized to use this command.")
        except TelegramError:
            logger.error("Failed to send unauthorized message")
        return

    if not USER_CHANNELS:
        try:
            await update.message.reply_text("📊 No data has been saved by any user yet.")
        except TelegramError:
            logger.error("Failed to send no data message")
        return

    response_lines = ["📊 *BOT DATA DUMP*\n"]

    total_channels = 0
    total_groups = 0

    for uid, channels in USER_CHANNELS.items():
        response_lines.append(f"👤 *User ID:* `{uid}`")
        if not channels:
            response_lines.append("  ╰─ No channels/groups saved.")
        else:
            for ch in channels:
                title = ch.get("title") or "[No Title]"
                ch_id = ch.get("id")
                username = f"@{ch['username']}" if ch.get("username") else "N/A"
                chat_type = ch.get("type", "channel")

                if chat_type in ["group", "supergroup"]:
                    total_groups += 1
                    icon = "👥"
                else:
                    total_channels += 1
                    icon = "📢"

                response_lines.append(f"  {icon} *{chat_type.upper()}:* {title}")
                response_lines.append(f"    - `ID`: `{ch_id}`")
                response_lines.append(f"    - `Username`: {username}")

                # Show active features
                active_features = []
                if ch.get("auto_button", {}).get("status") == "active":
                    active_features.append("🔘 Buttons")
                if ch.get("auto_captions", {}).get("status") == "active":
                    active_features.append("💬 Captions")
                if ch.get("auto_reactions", {}).get("status") == "active":
                    active_features.append("❤️ Reactions")
                if ch.get("auto_watermark", {}).get("status") == "active":
                    active_features.append("🖼️ Watermark")

                if active_features:
                    response_lines.append(f"    - `Active`: {', '.join(active_features)}")

        response_lines.append("")

    # Add summary
    response_lines.append(f"📊 *SUMMARY*")
    response_lines.append(f"Total Users: {len(USER_CHANNELS)}")
    response_lines.append(f"Total Channels: {total_channels}")
    response_lines.append(f"Total Groups: {total_groups}")

    response_text = "\n".join(response_lines)

    try:
        if len(response_text) > 4000:
            for i in range(0, len(response_text), 4000):
                await update.message.reply_markdown(response_text[i:i+4000])
        else:
            await update.message.reply_markdown(response_text)
    except TelegramError as e:
        logger.error(f"Error sending dump data: {e}")


# --- Main ---
def main():
    try:
        Config.validate()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return

    load_data()

    builder = Application.builder().token(Config.BOT_TOKEN)
    app = builder.build()

    app.bot_data['MAIN_ADMIN_ID'] = Config.MAIN_ADMIN_ID

    # Add global error handler
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log errors caused by updates."""
        logger.error(f"Exception while handling an update: {context.error}")

        # Try to notify the user
        if update and hasattr(update, 'effective_message') and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "⚠️ An error occurred. The bot is still running.\n"
                    "Please try again or contact support."
                )
            except Exception:
                pass  # Ignore errors in error handler

    # Register error handler
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("dumpdata", dump_data))

    app.add_handler(CallbackQueryHandler(button_router))

    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, handle_forwarded_message))

    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.ANIMATION) & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_user_message))

    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^@"), handle_text_channel_username))

    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, handle_channel_post))  # Also handle groups

    logger.info("=" * 50)
    logger.info("🤖 Bot starting...")
    logger.info("=" * 50)
    print("\n✓ Bot is running... Press Ctrl+C to stop\n")

    try:
        app.run_polling()
    except KeyboardInterrupt:
        logger.info("\n🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        save_data()
        logger.info("✓ Data saved. Bot shutdown complete")


if __name__ == "__main__":
    main()