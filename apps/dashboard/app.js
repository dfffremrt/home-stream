// Все запросы идут на тот же хост, что и страница — работает и на Mac, и на телефоне.
// Сервер сам проксирует внешние API.

// ============ ЧАСЫ ============
let timeOffset = 0;

async function syncTime() {
  try {
    const res = await fetch('/api/time');
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    if (typeof data.unixtime !== 'number' || data.unixtime <= 0) {
      throw new Error('Нет unixtime');
    }

    // unixtime — абсолютный момент в UTC.
    // new Date(ms) + toLocaleTimeString сам покажет локальное время.
    const serverMs = data.unixtime * 1000;
    timeOffset = serverMs - Date.now();

    document.getElementById('tz').textContent = '🕐 синхронизировано с сервером';
  } catch (e) {
    console.warn('Время не синхронизировано:', e);
    document.getElementById('tz').textContent = '⚠️ системное время';
  }
}

function updateClock() {
  const now = new Date(Date.now() + timeOffset);
  if (isNaN(now.getTime())) {
    document.getElementById('time').textContent = '--:--:--';
    document.getElementById('date').textContent = '—';
    return;
  }
  document.getElementById('time').textContent = now.toLocaleTimeString('ru-RU');
  document.getElementById('date').textContent = now.toLocaleDateString('ru-RU', {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  });
}

syncTime().then(updateClock);
setInterval(syncTime, 5 * 60 * 1000);
setInterval(updateClock, 1000);
updateClock();


// ============ ПОГОДА ============
const MY_CITY = 'Москва';

async function loadWeather() {
  const el = document.getElementById('weather');
  try {
    const res = await fetch('/api/weather');
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    const cur = data.current;
    const daily = data.daily;

    el.innerHTML = `
      <div class="weather-main">${Math.round(cur.temperature_2m)}°</div>
      <div class="weather-desc">${weatherDesc(cur.weather_code)} · ${MY_CITY}</div>
      <div class="weather-row"><span>Мин / Макс</span><span>${Math.round(daily.temperature_2m_min[0])}° / ${Math.round(daily.temperature_2m_max[0])}°</span></div>
      <div class="weather-row"><span>Влажность</span><span>${cur.relative_humidity_2m}%</span></div>
      <div class="weather-row"><span>Ветер</span><span>${cur.wind_speed_10m} км/ч</span></div>
    `;
  } catch (e) {
    el.textContent = 'Не удалось загрузить погоду 😢';
    console.error('Погода:', e);
  }
}

function weatherDesc(code) {
  const map = {
    0:'Ясно ☀️',1:'В основном ясно 🌤️',2:'Переменная облачность ⛅',3:'Пасмурно ☁️',
    45:'Туман 🌫️',48:'Иней 🌫️',51:'Лёгкая морось 🌦️',53:'Морось 🌦️',55:'Сильная морось 🌦️',
    61:'Слабый дождь 🌧️',63:'Дождь 🌧️',65:'Сильный дождь 🌧️',
    71:'Слабый снег 🌨️',73:'Снег 🌨️',75:'Сильный снег ❄️',
    80:'Ливень 🌦️',81:'Ливень 🌧️',82:'Сильный ливень ⛈️',
    95:'Гроза ⛈️',96:'Гроза с градом ⛈️',99:'Сильная гроза ⛈️',
  };
  return map[code] || '—';
}

loadWeather();
setInterval(loadWeather, 30 * 60 * 1000);


// ============ КУРСЫ ============
async function loadCurrency() {
  const el = document.getElementById('currency');
  try {
    const res = await fetch('/api/currency');
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    const wanted = ['USD', 'EUR', 'CNY', 'KZT'];
    el.innerHTML = wanted
      .filter(code => data.Valute[code])
      .map(code => {
        const v = data.Valute[code];
        return `<div class="currency-row">
          <span class="currency-code">${code}</span>
          <span>${v.Value.toFixed(2)} ₽</span>
        </div>`;
      }).join('');
  } catch (e) {
    el.textContent = 'Курсы недоступны 😢';
    console.error(e);
  }
}

loadCurrency();
setInterval(loadCurrency, 60 * 60 * 1000);


// ============ ССЫЛКИ (в localStorage — они локальные для каждого устройства) ============
const LINKS_KEY = 'dashboard.links';
const DEFAULT_LINKS = [
  { name: '▶️ YouTube',  url: 'https://www.youtube.com' },
  { name: '💬 MAX',      url: 'https://web.max.ru/' },
  { name: '✈️ Telegram', url: 'https://web.telegram.org' },
];

function getLinks() {
  const stored = localStorage.getItem(LINKS_KEY);
  if (!stored) {
    localStorage.setItem(LINKS_KEY, JSON.stringify(DEFAULT_LINKS));
    return DEFAULT_LINKS;
  }
  return JSON.parse(stored);
}
function saveLinks(links) {
  localStorage.setItem(LINKS_KEY, JSON.stringify(links));
  renderLinks();
}
function renderLinks() {
  const container = document.getElementById('links');
  const links = getLinks();
  if (links.length === 0) {
    container.innerHTML = '<span style="color:var(--muted);font-size:13px">Пока пусто. Добавь первую ссылку 👇</span>';
    return;
  }
  container.innerHTML = links.map((l, i) => `
    <a class="link-item" href="${l.url}" target="_blank" rel="noopener">
      ${escapeHtml(l.name)}
      <span class="del" data-index="${i}">×</span>
    </a>
  `).join('');
  container.querySelectorAll('.del').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const idx = Number(btn.dataset.index);
      const links = getLinks();
      links.splice(idx, 1);
      saveLinks(links);
    });
  });
}
document.getElementById('link-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const name = document.getElementById('link-name').value.trim();
  const url = document.getElementById('link-url').value.trim();
  if (!name || !url) return;
  const links = getLinks();
  links.push({ name, url });
  saveLinks(links);
  e.target.reset();
});
function escapeHtml(str) {
  return str.replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;',
  }[c]));
}
renderLinks();


// ============ ЗАМЕТКИ (сохраняются на сервере — общие для всех устройств) ============
const notesEl = document.getElementById('notes');
const statusEl = document.getElementById('notes-status');
let saveTimer;

// Загружаем заметки с сервера
(async () => {
  try {
    const res = await fetch('/api/notes');
    const data = await res.json();
    notesEl.value = data.text || '';
  } catch (e) {
    console.warn('Не удалось загрузить заметки:', e);
  }
})();

notesEl.addEventListener('input', () => {
  clearTimeout(saveTimer);
  statusEl.textContent = 'Печатаю...';
  saveTimer = setTimeout(async () => {
    try {
      await fetch('/api/notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: notesEl.value }),
      });
      statusEl.textContent = 'Сохранено ✓';
      setTimeout(() => statusEl.textContent = '', 1500);
    } catch (e) {
      statusEl.textContent = 'Ошибка сохранения';
    }
  }, 500);
});


// ============ ЧАТ ============
const CHAT_NAME_KEY = 'dashboard.chat.name';
const chatMessagesEl = document.getElementById('chat-messages');
const chatFormEl = document.getElementById('chat-form');
const chatInputEl = document.getElementById('chat-input');
const chatNameEl = document.getElementById('chat-name');

// Имя пользователя — из localStorage
let myName = localStorage.getItem(CHAT_NAME_KEY) || '';
chatNameEl.value = myName;

chatNameEl.addEventListener('input', () => {
  myName = chatNameEl.value.trim();
  localStorage.setItem(CHAT_NAME_KEY, myName);
});

// Отправка сообщения
chatFormEl.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = chatInputEl.value.trim();
  if (!text) return;

  // Если имени нет — спросим
  if (!myName) {
    const name = prompt('Как тебя зовут?');
    if (name) {
      myName = name.trim();
      localStorage.setItem(CHAT_NAME_KEY, myName);
      chatNameEl.value = myName;
    }
  }

  chatInputEl.value = '';
  try {
    await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: myName || 'Аноним', text }),
    });
    await loadChat();
  } catch (e) {
    console.error('Ошибка отправки:', e);
  }
});

// Загрузка и отрисовка сообщений
let lastChatRender = 0;
let lastMsgCount = -1;

async function loadChat() {
  try {
    const res = await fetch('/api/chat');
    const data = await res.json();
    const messages = data.messages || [];

    // Не перерисовываем, если ничего не изменилось (но только если не «мои» новые)
    if (messages.length === lastMsgCount && Date.now() - lastChatRender < 2000) {
      return;
    }
    lastMsgCount = messages.length;
    lastChatRender = Date.now();

    if (messages.length === 0) {
      chatMessagesEl.innerHTML = '<div class="chat-empty">Пока нет сообщений. Напиши первое 👋</div>';
      return;
    }

    // Проверяем, надо ли проскроллить вниз
    const wasAtBottom = chatMessagesEl.scrollHeight - chatMessagesEl.scrollTop - chatMessagesEl.clientHeight < 60;

    chatMessagesEl.innerHTML = messages.map(m => {
      const isMine = myName && m.name === myName;
      return `
        <div class="chat-msg ${isMine ? 'mine' : ''}">
          <div class="chat-msg-meta">
            <span class="chat-msg-name">${escapeHtml(m.name)}</span>
            <span>${formatTime(m.ts)}</span>
          </div>
          <div class="chat-msg-text">${escapeHtml(m.text)}</div>
        </div>
      `;
    }).join('');

    if (wasAtBottom) {
      chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
    }
  } catch (e) {
    console.error('Чат не загрузился:', e);
  }
}

function formatTime(ts) {
  const d = new Date(ts * 1000);
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  if (isToday) {
    return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }) +
    ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

// Очистка чата
document.getElementById('chat-clear').addEventListener('click', async () => {
  if (!confirm('Очистить весь чат?')) return;
  try {
    await fetch('/api/chat/clear', { method: 'POST' });
    lastMsgCount = -1;
    await loadChat();
  } catch (e) {
    console.error(e);
  }
});

// Первая загрузка + автообновление каждые 2 секунды
loadChat();
setInterval(loadChat, 2000);
