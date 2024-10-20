import os
import logging
import random
import json
import http.client  # Для управления логгированием HTTP-запросов

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from sqlhelper import Base, User, Post, Settings

# Инициализация логирования
logging.basicConfig(level=logging.WARNING,
                    format='%(asctime)s - %(levelname)s - %(message)s')  # Установлен уровень WARNING

# Установка уровня логирования для библиотеки Telegram
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

# Установка уровня логирования для HTTP
http.client.HTTPConnection.debuglevel = 0  # Отключение отладки HTTP-запросов

# Инициализация базы данных
print('[Predlozhka] Initializing database...')
engine = create_engine('sqlite:///database.db')
Base.metadata.create_all(engine)
Session = scoped_session(sessionmaker(bind=engine))

print('[Predlozhka] Initializing Telegram API...')
token = '7262732857:AAFR3KEN4ymIXsJVUrflugsWlcSj7NxTRqk'
application = Application.builder().token(token).build()

print('[Predlozhka] Creating temp folder...')
if not os.path.exists('temp'):
    os.makedirs('temp')

# Остальная логика бота
session = Session()
settings = session.query(Settings).first()
if not settings:
    settings = Settings(False, None, None)
    session.add(settings)

initialized = settings.initialized
target_channel = settings.target_channel

if initialized:
    if target_channel:
        print(f'[Predlozhka] Settings...[OK], target_channel: {target_channel}')
    elif settings.initializer_id:
        application.bot.send_message(settings.initializer_id, 'Warning! No target channel specified.')
else:
    print('[Predlozhka][CRITICAL] Bot is not initialized! Waiting for initializer...')
session.commit()
session.close()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = Session()
    if not db.query(User).filter_by(user_id=update.effective_user.id).first():
        db.add(User(user_id=update.effective_user.id))
    await update.message.reply_text('Добро пожаловать! Отправьте изображение для предложения поста.')
    db.commit()


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print('[Predlozhka][photo_handler] Image accepted, downloading...')
    db = Session()

    photo = update.message.photo[-1]
    path = f'temp/_{random.randint(1, 100000000000)}_{photo.file_id}.jpg'
    file = await photo.get_file()
    await file.download_to_drive(path)

    post = Post(update.effective_user.id, path, update.message.caption)
    db.add(post)
    db.commit()

    buttons = [
        [InlineKeyboardButton('✅', callback_data=json.dumps({'post': post.post_id, 'action': 'accept'})),
         InlineKeyboardButton('❌', callback_data=json.dumps({'post': post.post_id, 'action': 'decline'}))]
    ]

    for admin in db.query(User).filter_by(is_admin=1).all():
        try:
            message = await context.bot.send_photo(
                admin.user_id, open(post.attachment_path, 'rb'),
                caption=post.text, reply_markup=InlineKeyboardMarkup(buttons)
            )
            post.messages.append({'admin_id': admin.user_id, 'message_id': message.message_id})
        except Exception as e:
            print(f'[photo_handler][ERROR] Failed to send to admin {admin.user_id}: {e}')

    db.commit()
    db.close()

    await update.message.reply_text('Пост отправлен администраторам.')


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"Received callback: {update.callback_query.data}")  # Заменено на logging

    db = Session()
    user = db.query(User).filter_by(user_id=update.effective_user.id).first()

    if user and user.is_admin:
        data = json.loads(update.callback_query.data)
        post = db.query(Post).filter_by(post_id=data['post']).first()

        logging.info(
            f"Admin {user.user_id} is processing post {data['post']} with action {data['action']}")  # Заменено на logging

        if post:
            if data['action'] == 'accept':
                logging.info(f"Admin {user.user_id} accepted post {data['post']}")  # Заменено на logging

                try:
                    await context.bot.send_photo(target_channel, open(post.attachment_path, 'rb'), caption=post.text)
                    await update.callback_query.answer('✅ Пост опубликован')
                    await context.bot.send_message(post.owner_id, 'Ваш пост опубликован.')

                    # Удаляем кнопки из сообщения администратора
                    for message_data in post.messages:
                        try:
                            await context.bot.edit_message_reply_markup(
                                chat_id=message_data['admin_id'],
                                message_id=message_data['message_id'],
                                reply_markup=InlineKeyboardMarkup([])  # Устанавливаем пустую разметку
                            )
                        except Exception as e:
                            logging.error(
                                f'Ошибка при удалении кнопок для {message_data["admin_id"]}: {e}')  # Заменено на logging
                except Exception as e:
                    logging.error(f"Ошибка при отправке поста: {e}")  # Заменено на logging

            elif data['action'] == 'decline':
                logging.info(f"Admin {user.user_id} declined post {data['post']}")  # Заменено на logging

                if not post.messages:
                    logging.warning(f"No messages found for post {data['post']}.")  # Заменено на logging

                # Удаляем кнопки из сообщения администратора
                for message_data in post.messages:
                    try:
                        await context.bot.edit_message_reply_markup(
                            chat_id=message_data['admin_id'],
                            message_id=message_data['message_id'],
                            reply_markup=InlineKeyboardMarkup([])  # Устанавливаем пустую разметку
                        )
                    except Exception as e:
                        logging.error(
                            f'Error while removing buttons for {message_data["admin_id"]}: {e}')  # Заменено на logging

                await update.callback_query.answer('Пост отклонен')

                # Удаление временного файла
                if os.path.exists(post.attachment_path):
                    os.remove(post.attachment_path)
                    logging.info(f"Удалён временный файл {post.attachment_path}")  # Заменено на logging

                db.delete(post)
                logging.info(f"Post {data['post']} deleted from database.")  # Заменено на logging
        else:
            await update.callback_query.answer('Пост не найден.')
            logging.warning(f"Post {data['post']} not found.")  # Заменено на logging

    else:
        await update.callback_query.answer('У вас нет прав для этой операции.')
        logging.warning(f"User {update.effective_user.id} attempted unauthorized access.")  # Заменено на logging

    db.commit()
    db.close()


async def initialize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global initialized, target_channel
    db = Session()
    if not initialized:
        initialized = True
        params = update.message.text.replace('/init ', '').split(';')
        target_channel = params[0].strip()
        user_ids = [int(uid.strip()) for uid in params[1:]]
        settings = db.query(Settings).first()
        settings.initialized = True
        settings.target_channel = target_channel

        for uid in user_ids:
            user = db.query(User).filter_by(user_id=uid).first()
            if user:
                user.is_admin = 1
            else:
                db.add(User(user_id=uid, is_admin=1))

        db.commit()
        await update.message.reply_text('Бот инициализирован.')
    else:
        await update.message.reply_text('Бот уже инициализирован.')


print('[Predlozhka] All init related stuff done. Waiting for something to happen...')

application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler('init', initialize))
application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
application.add_handler(CallbackQueryHandler(callback_handler))

application.run_polling()
