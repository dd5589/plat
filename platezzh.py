import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import LabeledPrice, ReplyKeyboardRemove
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import asyncpg

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация (ЗАМЕНИТЕ НА СВОИ ДАННЫЕ)
BOT_TOKEN = "8474649656:AAG5motuAPtiezNfxG0yT8AFW4732ZIDTY4"
PAYMENT_TOKEN = "2051251535:TEST:OTk5MDA4ODgxLTAwNQ"  # Токен от @BotFather для платежей
DB_USER = "postgres"
DB_PASSWORD = "callofdutyer"
DB_NAME = "ticket_bot"
DB_HOST = "localhost"
DB_PORT = "5432"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Описание билетов
ticket_types = {
    "dancefloor": {"name": "Танцпол", "price": 1999},
    "pair": {"name": "Парный танцпол", "price": 3799},
    "early": {"name": "Танцпол (Ранний вход)", "price": 2399}
}

# Состояния FSM
class TicketState(StatesGroup):
    waiting_for_quantity = State()

# Подключение к PostgreSQL
async def create_db_pool():
    return await asyncpg.create_pool(
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        host=DB_HOST,
        port=DB_PORT
    )

# Инициализация базы данных
async def init_db(conn):
    # Создаем таблицу билетов
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS tickets (
        ticket_type VARCHAR(50) PRIMARY KEY,
        stock INTEGER NOT NULL
    );
    """)
    
    # Создаем таблицу покупок
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS purchases (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        username VARCHAR(100),
        full_name VARCHAR(100),
        ticket_type VARCHAR(50) NOT NULL,
        quantity INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        purchase_date TIMESTAMP DEFAULT NOW()
    );
    """)
    
    # Инициализируем остатки билетов
    for ticket_type, info in ticket_types.items():
        await conn.execute("""
        INSERT INTO tickets (ticket_type, stock)
        VALUES ($1, 10)
        ON CONFLICT (ticket_type) DO UPDATE SET stock = EXCLUDED.stock
        """, ticket_type)

# Получение текущих остатков
async def get_ticket_stocks(conn):
    stocks = {}
    records = await conn.fetch("SELECT ticket_type, stock FROM tickets")
    for record in records:
        stocks[record['ticket_type']] = record['stock']
    return stocks

# Обновление остатков
async def update_ticket_stock(conn, ticket_type, quantity):
    await conn.execute("""
    UPDATE tickets SET stock = stock - $1 
    WHERE ticket_type = $2
    """, quantity, ticket_type)
    return await conn.fetchval("SELECT stock FROM tickets WHERE ticket_type = $1", ticket_type)

# Команда /start
@dp.message(Command("start"))
async def start(message: types.Message):
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="🎫 Купить билеты"))
    builder.add(types.KeyboardButton(text="ℹ️ Информация о мероприятии"))
    builder.add(types.KeyboardButton(text="📊 Мои покупки"))
    
    await message.answer(
        "Добро пожаловать на мероприятие madk1d!\n"
        "Выберите действие:",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )

# Информация о мероприятии
@dp.message(F.text == "ℹ️ Информация о мероприятии")
async def event_info(message: types.Message):
    await message.answer(
        "🎤 madk1d | 9 Августа | Новосибирск\n"
        "📅 Суббота, 9 августа 2025\n"
        "🕒 18:00 - 22:00\n"
        "📍 Руки ВВерх! Бар, Красный проспект, 37\n"
        "🔞 Возрастное ограничение: 18+"
    )

# Показать билеты
@dp.message(F.text == "🎫 Купить билеты")
async def show_tickets(message: types.Message):
    async with db_pool.acquire() as conn:
        stocks = await get_ticket_stocks(conn)
        
        builder = InlineKeyboardBuilder()
        for ticket_type, info in ticket_types.items():
            stock = stocks.get(ticket_type, 0)
            if stock > 0:
                builder.add(types.InlineKeyboardButton(
                    text=f"{info['name']} ({stock} шт.) - {info['price']} руб.",
                    callback_data=f"ticket_{ticket_type}"
                ))
        
        builder.adjust(1)
        await message.answer("🎟 Доступные билеты:", reply_markup=builder.as_markup())

# Обработка выбора билета
@dp.callback_query(F.data.startswith("ticket_"))
async def select_ticket(callback: types.CallbackQuery, state: FSMContext):
    ticket_type = callback.data.split("_")[1]
    
    if ticket_type not in ticket_types:
        await callback.answer("Неизвестный тип билета", show_alert=True)
        return
    
    async with db_pool.acquire() as conn:
        stock = await conn.fetchval("SELECT stock FROM tickets WHERE ticket_type = $1", ticket_type)
        
        if not stock or stock <= 0:
            await callback.answer("Эти билеты закончились!", show_alert=True)
            return
        
        await state.update_data(ticket_type=ticket_type)
        await state.set_state(TicketState.waiting_for_quantity)
        
        ticket = ticket_types[ticket_type]
        await callback.message.answer(
            f"Вы выбрали: {ticket['name']}\n"
            f"Цена: {ticket['price']} руб.\n"
            f"Доступно: {stock} шт.\n\n"
            "Введите количество билетов:",
            reply_markup=ReplyKeyboardRemove()
        )

# Обработка количества билетов
@dp.message(TicketState.waiting_for_quantity, F.text.isdigit())
async def process_quantity(message: types.Message, state: FSMContext):
    try:
        quantity = int(message.text)
        if quantity <= 0:
            await message.answer("Пожалуйста, введите число больше 0")
            return
        
        data = await state.get_data()
        ticket_type = data.get('ticket_type')
        
        if not ticket_type:
            await message.answer("Ошибка: не выбран тип билета. Начните заново.")
            await state.clear()
            return
        
        async with db_pool.acquire() as conn:
            stock = await conn.fetchval("SELECT stock FROM tickets WHERE ticket_type = $1", ticket_type)
            
            if stock < quantity:
                await message.answer(f"Доступно только {stock} шт. Пожалуйста, введите меньшее количество.")
                return
            
            ticket = ticket_types[ticket_type]
            amount = ticket['price'] * quantity
            
            # Отправляем счет для оплаты
            await bot.send_invoice(
                chat_id=message.chat.id,
                title=f"Билеты: {ticket['name']}",
                description=f"Количество: {quantity} шт.",
                payload=f"{ticket_type}_{quantity}_{message.from_user.id}",
                provider_token=PAYMENT_TOKEN,
                currency="RUB",
                prices=[LabeledPrice(label=f"{quantity} билет(а)", amount=amount * 100)],
                need_phone_number=True,
                need_email=True
            )
            
            await state.clear()
            
    except ValueError:
        await message.answer("Пожалуйста, введите число")
    except Exception as e:
        logger.error(f"Ошибка при обработке количества: {e}")
        await message.answer("Произошла ошибка. Пожалуйста, попробуйте позже.")

# Подтверждение платежа
@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

# Успешный платеж
@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    try:
        payload = message.successful_payment.invoice_payload
        parts = payload.split('_')
        if len(parts) < 3:
            raise ValueError("Неверный формат payload")
            
        ticket_type = parts[0]
        quantity = int(parts[1])
        user_id = int(parts[2])
        
        # Проверяем, что платеж соответствует пользователю
        if message.from_user.id != user_id:
            await message.answer("Ошибка: несоответствие пользователя")
            return
        
        # Получаем информацию о билете
        ticket_info = ticket_types.get(ticket_type)
        if not ticket_info:
            await message.answer("Ошибка: неизвестный тип билета")
            return
        
        amount = message.successful_payment.total_amount // 100
        
        # Сохраняем покупку в базе данных
        async with db_pool.acquire() as conn:
            # Обновляем остатки
            await update_ticket_stock(conn, ticket_type, quantity)
            
            # Сохраняем покупку
            await conn.execute("""
            INSERT INTO purchases (
                user_id, username, full_name, 
                ticket_type, quantity, amount
            ) VALUES ($1, $2, $3, $4, $5, $6)
            """, 
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
            ticket_type,
            quantity,
            amount)
        
        # Отправляем подтверждение
        await message.answer(
            "✅ Платеж прошел успешно!\n"
            f"Вы приобрели {quantity} билет(а) на {ticket_info['name']}\n"
            f"Сумма: {amount} руб.\n\n"
            "Ваши билеты будут отправлены вам на email в течение 24 часов."
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке платежа: {e}")
        await message.answer(
            "Платеж прошел, но возникла проблема с обработкой. "
            "Пожалуйста, свяжитесь с поддержкой и предоставьте этот код: "
            f"{message.successful_payment.telegram_payment_charge_id}"
        )

# Показать историю покупок
@dp.message(F.text == "📊 Мои покупки")
async def show_purchases(message: types.Message):
    try:
        async with db_pool.acquire() as conn:
            purchases = await conn.fetch("""
            SELECT ticket_type, quantity, amount, purchase_date 
            FROM purchases 
            WHERE user_id = $1 
            ORDER BY purchase_date DESC
            """, message.from_user.id)
        
        if not purchases:
            await message.answer("У вас пока нет покупок.")
            return
        
        response = ["📋 Ваши покупки:\n"]
        for purchase in purchases:
            ticket_name = ticket_types.get(purchase['ticket_type'], {}).get('name', 'Неизвестный билет')
            response.append(
                f"🎫 {ticket_name}\n"
                f"Количество: {purchase['quantity']} шт.\n"
                f"Сумма: {purchase['amount']} руб.\n"
                f"Дата: {purchase['purchase_date'].strftime('%d.%m.%Y %H:%M')}\n"
            )
        
        await message.answer("\n".join(response))
        
    except Exception as e:
        logger.error(f"Ошибка при получении покупок: {e}")
        await message.answer("Произошла ошибка при получении истории покупок.")

# Основная функция
async def main():
    global db_pool
    
    try:
        # Создаем пул подключений к БД
        db_pool = await create_db_pool()
        logger.info("Подключение к PostgreSQL установлено")
        
        # Инициализируем базу данных
        async with db_pool.acquire() as conn:
            await init_db(conn)
            stocks = await get_ticket_stocks(conn)
            logger.info(f"Остатки билетов инициализированы: {stocks}")
        
        # Запускаем бота
        logger.info("Бот запущен")
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
    finally:
        if db_pool:
            await db_pool.close()
            logger.info("Подключение к PostgreSQL закрыто")

if __name__ == '__main__':
    asyncio.run(main())