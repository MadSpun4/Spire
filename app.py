from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime
from models import db, User, Message, PrivateMessage
import io

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'your_secret_key'

db.init_app(app)

socketio = SocketIO(app)

with app.app_context():
    db.create_all()

@app.route('/')
def index():
    """
    Главная страница мессенджера.
    Отображает список сообщений и форму для отправки новых сообщений.
    """
    if 'username' in session:
        return render_template('index.html', username=session['username'])
    return redirect(url_for('login'))

@app.route('/general', methods=['GET', 'POST'])
def general():
    if 'username' in session:
        messages = Message.query.order_by(Message.timestamp.asc()).all()
        return render_template('general.html', messages=messages, username=session['username'])
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    Страница входа.
    Принимает данные для входа и проверяет их.
    """
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session['username'] = username
            session['user_id'] = user.id
            return redirect(url_for('index'))
        flash('Invalid username or password')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    """
    Страница регистрации.
    Принимает данные для регистрации и создает нового пользователя.
    """
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash('User already exists')
            return redirect(url_for('register'))
        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        session['username'] = username
        session['user_id'] = new_user.id
        return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    """
    Выход из системы.
    Удаляет пользователя из сессии.
    """
    session.pop('username', None)
    session.pop('user_id', None)
    return redirect(url_for('login'))

@app.route('/send', methods=['POST'])
def send_message():
    if 'username' not in session:
        return redirect(url_for('login'))
    username = session['username']
    message = request.form['message']
    if message:
        new_message = Message(username=username, message=message)
        db.session.add(new_message)
        db.session.commit()
        socketio.emit('new_message',  {'username': username, 'message': message, 'timestamp': new_message.timestamp.strftime('%Y-%m-%d %H:%M:%S')}, room='general')
    return redirect(url_for('general'))

@app.route('/recent', methods=['GET', 'POST'])
def recent():
    """
    Страница со списком пользователей.
    """
    if 'username' not in session:
        return redirect(url_for('login'))
    
    search_query = request.form.get('search_query', '')
    current_user_id = session['user_id']
    
    # Найти пользователей, с которыми текущий пользователь взаимодействовал
    interacted_user_ids = db.session.query(
        PrivateMessage.sender_id
    ).filter(
        PrivateMessage.receiver_id == current_user_id
    ).union(
        db.session.query(
            PrivateMessage.receiver_id
        ).filter(
            PrivateMessage.sender_id == current_user_id
        )
    ).all()
    
    interacted_user_ids = [user_id for user_id, in interacted_user_ids]
    
    if search_query:
        users = User.query.filter(
            User.id.in_(interacted_user_ids),
            User.username.like(f'%{search_query}%')
        ).all()
    else:
        users = User.query.filter(User.id.in_(interacted_user_ids)).all()
    
    return render_template('recent.html', users=users, search_query=search_query)

@app.route('/users', methods=['GET', 'POST'])
def users():
    """
    Страница со списком пользователей.
    """
    if 'username' not in session:
        return redirect(url_for('login'))
    
    search_query = request.form.get('search_query', '')
    if search_query:
        users = User.query.filter(User.username.like(f'%{search_query}%')).all()
    else:
        users = User.query.all()
    
    return render_template('users.html', users=users, search_query=search_query)

@app.route('/search_user', methods=['POST'])
def search_user():
    """
    Обработка поиска пользователя по имени.
    Перенаправляет на личный чат с найденным пользователем.
    """
    search_query = request.form.get('search_query')
    if search_query:
        user = User.query.filter_by(username=search_query).first()
        if user:
            return redirect(url_for('private_chat', user_id=user.id))
        else:
            flash('User not found')
    return redirect(url_for('users'))

@app.route('/chat/<int:user_id>', methods=['GET', 'POST'])
def private_chat(user_id):
    """
    Личный чат между пользователями.
    """
    if 'username' not in session:
        return redirect(url_for('login'))
    sender_id = session['user_id']
    receiver = User.query.get_or_404(user_id)
    if request.method == 'POST':
        message = request.form['message']
        if message:
            new_message = PrivateMessage(sender_id=sender_id, receiver_id=user_id, message=message)
            db.session.add(new_message)
            db.session.commit()
            socketio.emit('new_private_message', {
                'sender_id': sender_id,
                'receiver_id': user_id,
                'message': message,
                'timestamp': new_message.timestamp.strftime('%Y-%m-%d %H:%M:%S'),  # Преобразование datetime в строку
                'sender_username': session['username']
            }, room=f'user_{user_id}')
    messages = PrivateMessage.query.filter(
        ((PrivateMessage.sender_id == sender_id) & (PrivateMessage.receiver_id == user_id)) |
        ((PrivateMessage.sender_id == user_id) & (PrivateMessage.receiver_id == sender_id))
    ).order_by(PrivateMessage.timestamp.asc()).all()
    return render_template('chat.html', messages=messages, receiver=receiver)

@socketio.on('join')
def handle_join(data):
    if data["user_id"] == 'general':
        room = 'general'
    else: room = f'user_{data["user_id"]}'
    join_room(room)

@socketio.on('leave')
def handle_leave(data):
    if data["user_id"] == 'general':
        room = 'general'
    else: room = f'user_{data["user_id"]}'
    leave_room(room)

@app.route('/download_chat/<int:user_id>')
def download_chat(user_id):
    """
    Маршрут для скачивания личного чата между пользователями.
    """
    if 'username' not in session:
        return redirect(url_for('login'))
    sender_id = session['user_id']
    receiver = User.query.get_or_404(user_id)
    messages = PrivateMessage.query.filter(
        ((PrivateMessage.sender_id == sender_id) & (PrivateMessage.receiver_id == user_id)) |
        ((PrivateMessage.sender_id == user_id) & (PrivateMessage.receiver_id == sender_id))
    ).order_by(PrivateMessage.timestamp.asc()).all()
    
    output = io.StringIO()
    for msg in messages:
        output.write(f"|{msg.timestamp}| {'You' if msg.sender_id == sender_id else receiver.username}: {msg.message}\n")
    
    output.seek(0)
    mem = io.BytesIO()
    mem.write(output.getvalue().encode('utf-8'))
    mem.seek(0)
    output.close()
    
    return send_file(mem, mimetype='text/plain', download_name='chat.txt', as_attachment=True)

if __name__ == '__main__':
    socketio.run(app, debug=True)
