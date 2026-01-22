import os
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import speech_recognition as sr
from pydub import AudioSegment
from pydub.utils import make_chunks
import tempfile
from concurrent.futures import ThreadPoolExecutor
import aiohttp
import aiofiles

# Logging sozlamalari
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Tokenni o'rnating
TELEGRAM_TOKEN = "8546408550:AAFMOOLm9qCZm6AiKoOfJyYxajov0aNVajo"

# Sozlamalar
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
CHUNK_DURATION_MS = 30000  # 30 soniya
DOWNLOAD_CHUNK_SIZE = 65536  # 64KB
MAX_WORKERS = 4

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot ishga tushganda yuboriladi"""
    await update.message.reply_text(
        "🎙️ Assalomu alaykum!\n\n"
        "Men audio fayllarni tezkor tekstga aylantiradigan botman.\n\n"
        "📎 Menga audio fayl yoki voice message yuboring.\n\n"
        "⚡ Xususiyatlar:\n"
        "• Katta fayllar uchun optimizatsiya\n"
        "• Parallel qayta ishlash\n"
        "• Tezkor yuklab olish\n"
        "• Maksimal hajm: 50MB\n\n"
        "🌐 Qo'llab-quvvatlanadigan tillar: O'zbek, Ingliz, Rus"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yordam buyrug'i"""
    await update.message.reply_text(
        "📖 Botdan foydalanish:\n\n"
        "1. Audio fayl yoki ovozli xabar yuboring\n"
        "2. Bot avtomatik qayta ishlaydi\n"
        "3. Natijani olasiz\n\n"
        "⚡ Tezkor ishlash uchun:\n"
        "• Sifatli audio yuboring\n"
        "• Shovqin kam bo'lsin\n"
        "• Aniq talaffuz\n\n"
        "📊 Maksimal hajm: 50MB"
    )

async def download_file_fast(file_url: str, destination: str, progress_callback=None):
    """Faylni tezkor yuklab olish (asinxron)"""
    try:
        timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(file_url) as response:
                if response.status != 200:
                    raise Exception(f"Yuklab olish xatosi: {response.status}")
                
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                
                async with aiofiles.open(destination, 'wb') as f:
                    async for chunk in response.content.iter_chunked(DOWNLOAD_CHUNK_SIZE):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        
                        if progress_callback and total_size > 0:
                            progress = (downloaded / total_size) * 100
                            await progress_callback(progress)
                
                logger.info(f"Fayl muvaffaqiyatli yuklandi: {destination}")
                return True
                
    except Exception as e:
        logger.error(f"Yuklab olish xatosi: {e}")
        raise

def convert_to_wav_optimized(input_path: str) -> str:
    """Optimizatsiyalangan audio konvertatsiya"""
    try:
        # Audio faylni yuklash
        audio = AudioSegment.from_file(input_path)
        
        # WAV formatga o'zgartirish (16kHz, mono - tezroq ishlash uchun)
        wav_path = input_path.rsplit('.', 1)[0] + '.wav'
        audio = audio.set_frame_rate(16000).set_channels(1)
        
        # Ovoz balandligini normalizatsiya qilish
        audio = audio.normalize()
        
        audio.export(wav_path, format='wav', parameters=["-ac", "1", "-ar", "16000"])
        
        return wav_path
    except Exception as e:
        logger.error(f"Konvertatsiya xatosi: {e}")
        raise

def transcribe_chunk(audio_chunk, chunk_index: int, language: str = "uz-UZ"):
    """Bir qismni tekstga aylantirish"""
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 300
    recognizer.dynamic_energy_threshold = True
    
    try:
        # Vaqtinchalik fayl yaratish
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_wav:
            temp_path = temp_wav.name
            audio_chunk.export(temp_path, format='wav')
        
        with sr.AudioFile(temp_path) as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.3)
            audio_data = recognizer.record(source)
        
        # Transkriptsiya
        try:
            text = recognizer.recognize_google(audio_data, language=language)
            return f"[{chunk_index}] {text}"
        except sr.UnknownValueError:
            return f"[{chunk_index}] [Tanilmadi]"
        except sr.RequestError as e:
            return f"[{chunk_index}] [Xato: {e}]"
    
    except Exception as e:
        logger.error(f"Chunk {chunk_index} xatosi: {e}")
        return f"[{chunk_index}] [Xatolik]"
    
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

async def transcribe_audio_parallel(audio_path: str, language: str = "uz-UZ", progress_callback=None):
    """Audio faylni parallel qayta ishlash"""
    try:
        # WAV formatga o'zgartirish
        if progress_callback:
            await progress_callback("🔄 Audio konvertatsiya qilinmoqda...")
        
        wav_path = convert_to_wav_optimized(audio_path)
        audio = AudioSegment.from_wav(wav_path)
        
        # Audio davomiyligini tekshirish
        duration_seconds = len(audio) / 1000
        
        if duration_seconds <= 30:
            # Qisqa audio uchun oddiy usul
            if progress_callback:
                await progress_callback("🎯 Qayta ishlanmoqda...")
            
            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio_data = recognizer.record(source)
            
            text = recognizer.recognize_google(audio_data, language=language)
            return text
        else:
            # Uzun audio uchun parallel qayta ishlash
            if progress_callback:
                await progress_callback(f"📊 Audio bo'laklarga bo'linmoqda... ({int(duration_seconds)}s)")
            
            chunks = make_chunks(audio, CHUNK_DURATION_MS)
            total_chunks = len(chunks)
            
            if progress_callback:
                await progress_callback(f"⚡ {total_chunks} ta bo'lak parallel ishlanmoqda...")
            
            # Parallel qayta ishlash
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                tasks = [
                    loop.run_in_executor(executor, transcribe_chunk, chunk, i, language)
                    for i, chunk in enumerate(chunks, 1)
                ]
                
                results = []
                for i, task in enumerate(asyncio.as_completed(tasks), 1):
                    result = await task
                    results.append(result)
                    if progress_callback:
                        progress = (i / total_chunks) * 100
                        await progress_callback(f"⏳ Jarayon: {int(progress)}% ({i}/{total_chunks})")
            
            # Natijalarni tartiblash va birlashtirish
            results.sort(key=lambda x: int(x.split(']')[0].replace('[', '')))
            final_text = ' '.join([r.split('] ', 1)[1] for r in results if '] ' in r])
            
            return final_text.strip()
    
    except sr.UnknownValueError:
        return "❌ Ovozni taniy olmadim. Iltimos, aniqroq audio yuboring."
    except sr.RequestError as e:
        return f"❌ Xizmat xatosi: {e}"
    except Exception as e:
        logger.error(f"Transkriptsiya xatosi: {e}")
        return f"❌ Xatolik: {str(e)}"
    
    finally:
        # Vaqtinchalik fayllarni o'chirish
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
            if 'wav_path' in locals() and os.path.exists(wav_path):
                os.remove(wav_path)
        except:
            pass

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Voice message larni qayta ishlash"""
    status_msg = await update.message.reply_text("⏬ Yuklab olinmoqda...")
    
    try:
        voice_file = await update.message.voice.get_file()
        
        # Vaqtinchalik fayl
        with tempfile.NamedTemporaryFile(delete=False, suffix='.ogg') as temp_file:
            temp_path = temp_file.name
        
        # Progress callback
        async def update_progress(progress):
            if isinstance(progress, str):
                await status_msg.edit_text(progress)
            else:
                await status_msg.edit_text(f"⏬ Yuklanmoqda: {int(progress)}%")
        
        # Faylni yuklab olish
        file_url = voice_file.file_path
        await download_file_fast(file_url, temp_path, update_progress)
        
        # Tekstga aylantirish
        text = await transcribe_audio_parallel(temp_path, progress_callback=update_progress)
        
        # Natijani yuborish
        await status_msg.delete()
        await update.message.reply_text(f"✅ Tayyor!\n\n📝 Matn:\n{text}")
        
    except Exception as e:
        logger.error(f"Voice xatosi: {e}")
        await status_msg.edit_text("❌ Xatolik yuz berdi. Qaytadan urinib ko'ring.")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Audio fayllarni qayta ishlash"""
    # Fayl hajmini tekshirish
    if update.message.audio.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(f"⚠️ Fayl juda katta! Maksimal hajm: {MAX_FILE_SIZE // (1024*1024)}MB")
        return
    
    status_msg = await update.message.reply_text("⏬ Katta fayl yuklanmoqda...")
    
    try:
        audio_file = await update.message.audio.get_file()
        file_name = update.message.audio.file_name or "audio.mp3"
        extension = file_name.split('.')[-1] if '.' in file_name else 'mp3'
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{extension}') as temp_file:
            temp_path = temp_file.name
        
        async def update_progress(progress):
            if isinstance(progress, str):
                await status_msg.edit_text(progress)
            else:
                await status_msg.edit_text(f"⏬ Yuklanmoqda: {int(progress)}%")
        
        file_url = audio_file.file_path
        await download_file_fast(file_url, temp_path, update_progress)
        
        text = await transcribe_audio_parallel(temp_path, progress_callback=update_progress)
        
        await status_msg.delete()
        await update.message.reply_text(f"✅ Tayyor!\n\n📝 Matn:\n{text}")
        
    except Exception as e:
        logger.error(f"Audio xatosi: {e}")
        await status_msg.edit_text("❌ Xatolik yuz berdi.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Audio hujjatlarni qayta ishlash"""
    document = update.message.document
    
    if document.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(f"⚠️ Fayl juda katta! Maksimal: {MAX_FILE_SIZE // (1024*1024)}MB")
        return
    
    audio_formats = ['mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac', 'wma']
    file_name = document.file_name.lower()
    
    if not any(file_name.endswith(f'.{fmt}') for fmt in audio_formats):
        await update.message.reply_text("⚠️ Faqat audio fayllar qabul qilinadi.")
        return
    
    status_msg = await update.message.reply_text("⏬ Fayl yuklanmoqda...")
    
    try:
        file = await document.get_file()
        extension = file_name.split('.')[-1]
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{extension}') as temp_file:
            temp_path = temp_file.name
        
        async def update_progress(progress):
            if isinstance(progress, str):
                await status_msg.edit_text(progress)
            else:
                await status_msg.edit_text(f"⏬ Yuklanmoqda: {int(progress)}%")
        
        file_url = file.file_path
        await download_file_fast(file_url, temp_path, update_progress)
        
        text = await transcribe_audio_parallel(temp_path, progress_callback=update_progress)
        
        await status_msg.delete()
        await update.message.reply_text(f"✅ Tayyor!\n\n📝 Matn:\n{text}")
        
    except Exception as e:
        logger.error(f"Document xatosi: {e}")
        await status_msg.edit_text("❌ Xatolik yuz berdi.")

def main():
    """Botni ishga tushirish"""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    logger.info("⚡ Tezkor bot ishga tushdi...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
