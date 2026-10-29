import {
  cancelRequest,
  deleteEvent,
  getEvents,
  getSettings,
  getTasks,
  getUpcomingReminders,
  getVoiceStatus,
  sendText,
  sendVoice,
  stopSpeech,
  updateEvent,
  updateSettings,
} from './api.js';

const app = document.querySelector('.js-app-container');
const statusEl = document.querySelector('.js-assistant-status');
const replyEl = document.querySelector('.js-assistant-reply');
const commandForm = document.querySelector('.js-command-form');
const commandInput = document.querySelector('.js-command-input');
const micButton = document.querySelector('.js-mic-button');
const operationCancelButton = document.querySelector('.js-operation-cancel');
const speechStopButton = document.querySelector('.js-speech-stop');
const micLabel = document.querySelector('.mic-button-label');
const calendarView = document.querySelector('.js-calendar-view');
const assistantPanel = document.querySelector('.assistant-panel');
const calendarHeading = document.querySelector('.js-calendar-heading');
const calendarGrid = document.querySelector('.js-calendar-grid');
const selectedDayHeading = document.querySelector('.js-selected-day-heading');
const eventsList = document.querySelector('.js-events-list');
const eventsCount = document.querySelector('.js-events-count');
const eventEditorOverlay = document.querySelector('.js-event-editor-overlay');
const eventEditorForm = document.querySelector('.js-event-editor-form');
const editorContent = document.querySelector('.js-editor-content');
const editorDateTime = document.querySelector('.js-editor-datetime');
const editorType = document.querySelector('.js-editor-type');
const editorFeedback = document.querySelector('.js-editor-feedback');
const deleteConfirmPanel = document.querySelector('.js-delete-confirm');
const deleteFeedback = document.querySelector('.js-delete-feedback');
const settingsOverlay = document.querySelector('.js-settings-overlay');
const settingsFeedback = document.querySelector('.js-settings-feedback');
const inputDeviceSelect = document.querySelector('.js-input-device');
const outputDeviceSelect = document.querySelector('.js-output-device');
const volumeSlider = document.querySelector('.js-volume-slider');
const volumeValue = document.querySelector('.js-volume-value');
const autostartSwitch = document.querySelector('.js-autostart-switch');
const autostartFeedback = document.querySelector('.js-autostart-feedback');
const wakeSwitch = document.querySelector('.js-wake-switch');
const wakeFeedback = document.querySelector('.js-wake-feedback');
const sidebar = document.querySelector('.js-sidebar');
const remindersList = document.querySelector('.js-reminders-list');
const remindersWindowLabel = document.querySelector('.js-reminders-window');
const tasksList = document.querySelector('.js-tasks-list');
const tasksToggleCompleted = document.querySelector('.js-tasks-toggle-completed');
const reminderDaysInput = document.querySelector('.js-reminder-days-input');
const reminderDaysValue = document.querySelector('.js-reminder-days-value');
const reminderDaysFeedback = document.querySelector('.js-reminder-days-feedback');

const months = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
const screenHideTimers = new WeakMap();
let visibleMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
let desktopApi = window.pywebview?.api || null;
let isRecording = false;
let backgroundWakeState = null;
let activeRequest = false;
let activeRequestController = null;
let activeRequestId = null;
let monthEvents = [];
let selectedDate = new Date();
let calendarLoadRequest = 0;
let editingEvent = null;
let eventEditTrigger = null;
let speechMonitorId = 0;
let lastWakeResponse = null;

// === Боковая панель (напоминания + задачи) ===
let appSettings = { reminder_days_ahead: 3 };
let upcomingReminders = [];
let allTasks = [];
let showCompletedTasks = true;
let sidebarRefreshHandle = null;

function setStatus(message, busy = false) {
  statusEl.textContent = message;
  document.body.classList.toggle('is-processing', busy);
}

function updateOperationCancelButton() {
  const isBackgroundThinking = Boolean(backgroundWakeState?.active_request_id);
  const isWakeRecording = Boolean(
    backgroundWakeState?.awaiting_command || backgroundWakeState?.phase === 'transcribing',
  );
  operationCancelButton.hidden = !(isRecording || activeRequest || isBackgroundThinking || isWakeRecording);
  operationCancelButton.title = isRecording || isWakeRecording ? 'Отменить запись' : 'Остановить обработку';
}

function showReply(message) {
  replyEl.textContent = message || '';
  replyEl.classList.toggle('has-content', Boolean(message));
}

// ===== Хелперы для боковой панели =====

function formatReminderDate(iso) {
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate();
  const time = dt.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  if (sameDay(dt, today)) return `Сегодня, ${time}`;
  if (sameDay(dt, tomorrow)) return `Завтра, ${time}`;
  return `${dt.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })}, ${time}`;
}

function formatDuration(ms) {
  const totalSec = Math.floor(ms / 1000);
  const days = Math.floor(totalSec / 86400);
  const hours = Math.floor((totalSec % 86400) / 3600);
  const minutes = Math.floor((totalSec % 3600) / 60);
  const seconds = totalSec % 60;
  if (days > 0) {
    return `${days} д. ${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
  }
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function formatOverdue(ms) {
  const totalSec = Math.floor(-ms / 1000);
  const days = Math.floor(totalSec / 86400);
  const hours = Math.floor((totalSec % 86400) / 3600);
  const minutes = Math.floor((totalSec % 3600) / 60);
  if (days > 0) return `просрочено на ${days} д.`;
  if (hours > 0) return `просрочено на ${hours} ч.`;
  if (minutes > 0) return `просрочено на ${minutes} мин.`;
  return 'просрочено';
}

function applyTimerLabel(el, diffMs, completed) {
  if (completed) {
    el.classList.add('is-completed');
    el.classList.remove('is-overdue');
    el.textContent = 'выполнено';
    return;
  }
  if (!el.dataset.deadline) {
    el.classList.remove('is-overdue', 'is-completed');
    el.textContent = 'без срока';
    return;
  }
  if (diffMs < 0) {
    el.classList.add('is-overdue');
    el.classList.remove('is-completed');
    el.textContent = formatOverdue(diffMs);
  } else {
    el.classList.remove('is-overdue', 'is-completed');
    el.textContent = `через ${formatDuration(diffMs)}`;
  }
  const parent = el.closest('.sidebar-item');
  if (parent) {
    if (diffMs < 0 && !completed) parent.classList.add('is-overdue');
    else parent.classList.remove('is-overdue');
  }
}

function renderReminders() {
  if (remindersWindowLabel) {
    remindersWindowLabel.textContent = `+${appSettings.reminder_days_ahead} дн.`;
  }
  if (!remindersList) return;
  if (!upcomingReminders.length) {
    remindersList.replaceChildren(Object.assign(document.createElement('p'), {
      className: 'sidebar-empty',
      textContent: 'Нет ближайших напоминаний.',
    }));
    return;
  }
  remindersList.replaceChildren();
  for (const item of upcomingReminders) {
    const row = document.createElement('div');
    row.className = 'sidebar-item sidebar-item-reminder';
    const content = document.createElement('div');
    content.className = 'sidebar-item-content';
    content.textContent = item.content;
    const meta = document.createElement('div');
    meta.className = 'sidebar-item-meta';
    const when = document.createElement('span');
    when.className = 'sidebar-item-when';
    when.textContent = item.datetime_iso ? formatReminderDate(item.datetime_iso) : 'Без времени';
    meta.append(when);
    row.append(content, meta);
    remindersList.append(row);
  }
}

function renderTasks() {
  if (!tasksList) return;
  const visible = showCompletedTasks ? allTasks : allTasks.filter((t) => !t.is_completed);
  if (!visible.length) {
    tasksList.replaceChildren(Object.assign(document.createElement('p'), {
      className: 'sidebar-empty',
      textContent: allTasks.length ? 'Нет активных задач.' : 'Задач нет.',
    }));
    return;
  }
  const now = Date.now();
  tasksList.replaceChildren();
  for (const item of visible) {
    const row = document.createElement('div');
    row.className = 'sidebar-item sidebar-item-task';
    if (item.is_completed) row.classList.add('is-completed');
    row.dataset.taskId = String(item.id);

    const content = document.createElement('div');
    content.className = 'sidebar-item-content';
    content.textContent = item.content;

    const meta = document.createElement('div');
    meta.className = 'sidebar-item-meta';

    const when = document.createElement('span');
    when.className = 'sidebar-item-when';
    if (item.datetime_iso) {
      when.textContent = new Date(item.datetime_iso).toLocaleString('ru-RU', {
        day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
      });
    } else {
      when.textContent = 'Без срока';
    }

    const timer = document.createElement('span');
    timer.className = 'sidebar-item-timer';
    timer.dataset.deadline = item.datetime_iso || '';
    timer.dataset.completed = item.is_completed ? '1' : '0';
    timer.setAttribute('aria-live', 'off');
    if (item.datetime_iso) {
      const diff = new Date(item.datetime_iso).getTime() - now;
      applyTimerLabel(timer, diff, Boolean(item.is_completed));
    } else {
      applyTimerLabel(timer, 0, Boolean(item.is_completed));
    }

    meta.append(when, timer);
    row.append(content, meta);
    tasksList.append(row);
  }
}

function tickCountdowns() {
  if (!tasksList) return;
  const now = Date.now();
  for (const el of tasksList.querySelectorAll('.sidebar-item-timer')) {
    const deadline = el.dataset.deadline;
    const completed = el.dataset.completed === '1';
    if (!deadline) {
      applyTimerLabel(el, 0, completed);
      continue;
    }
    const diff = new Date(deadline).getTime() - now;
    applyTimerLabel(el, diff, completed);
  }
}

async function loadAppSettings() {
  try {
    const settings = await getSettings();
    if (settings && typeof settings.reminder_days_ahead === 'number') {
      appSettings = { ...appSettings, ...settings };
    }
  } catch (error) {
    console.warn('Не удалось загрузить настройки:', error);
  }
  if (reminderDaysInput) {
    reminderDaysInput.value = String(appSettings.reminder_days_ahead);
  }
  if (reminderDaysValue) {
    reminderDaysValue.textContent = String(appSettings.reminder_days_ahead);
  }
}

async function loadSidebar() {
  try {
    const [reminders, tasks] = await Promise.all([
      getUpcomingReminders(appSettings.reminder_days_ahead),
      getTasks(true),
    ]);
    upcomingReminders = Array.isArray(reminders) ? reminders : [];
    allTasks = Array.isArray(tasks) ? tasks : [];
    renderReminders();
    renderTasks();
  } catch (error) {
    if (remindersList) {
      remindersList.replaceChildren(Object.assign(document.createElement('p'), {
        className: 'sidebar-empty',
        textContent: 'Не удалось загрузить напоминания.',
      }));
    }
    if (tasksList) {
      tasksList.replaceChildren(Object.assign(document.createElement('p'), {
        className: 'sidebar-empty',
        textContent: 'Не удалось загрузить задачи.',
      }));
    }
  }
}

// ====== Общая инициализация ======

async function stopCurrentSpeech() {
  speechMonitorId += 1;
  speechStopButton.hidden = true;
  try {
    await stopSpeech();
  } catch (error) {
    console.warn('Не удалось остановить озвучивание:', error);
  }
}

function monitorSpeech(queued) {
  const monitorId = ++speechMonitorId;
  if (!queued) {
    speechStopButton.hidden = true;
    return;
  }
  speechStopButton.hidden = false;
  setStatus('Нео говорит… Можно остановить озвучивание и прочитать ответ.');
  void (async () => {
    while (monitorId === speechMonitorId) {
      try {
        const state = await getVoiceStatus();
        if (monitorId !== speechMonitorId) return;
        const active = Boolean(state.speaking || state.queued_items);
        speechStopButton.hidden = !active;
        if (!active) return;
      } catch {
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 400));
    }
  })();
}

speechStopButton.addEventListener('click', async () => {
  speechStopButton.disabled = true;
  await stopCurrentSpeech();
  speechStopButton.disabled = false;
  setStatus('Озвучивание остановлено. Текст ответа остался на экране.');
});

operationCancelButton.addEventListener('click', async () => {
  operationCancelButton.disabled = true;
  try {
    if (isRecording && desktopApi) {
      await desktopApi.cancel_recording();
      isRecording = false;
      micButton.classList.remove('is-recording');
      micButton.setAttribute('aria-label', 'Начать запись');
      micLabel.textContent = 'Нажмите, чтобы говорить';
      setStatus('Запись отменена. Команда не отправлена.');
      return;
    }

    if (
      desktopApi
      && (backgroundWakeState?.awaiting_command || backgroundWakeState?.phase === 'transcribing')
    ) {
      const result = await desktopApi.cancel_wake_command();
      if (result.requested) setStatus('Запись отменена. Команда не отправлена.');
      return;
    }

    const requestId = activeRequestId || backgroundWakeState?.active_request_id;
    if (requestId) {
      try { await cancelRequest(requestId); } catch (error) {
        console.warn('Не удалось отправить отмену запроса:', error);
      }
    }
    activeRequestController?.abort();
    await stopCurrentSpeech();
    setStatus('Обработка отменена.');
    showReply('Команда отменена.');
  } finally {
    operationCancelButton.disabled = false;
    updateOperationCancelButton();
  }
});

async function waitForDesktopApi() {
  if (window.pywebview?.api) return window.pywebview.api;
  return new Promise((resolve) => {
    const timeout = window.setTimeout(() => resolve(null), 1800);
    window.addEventListener('pywebviewready', () => {
      window.clearTimeout(timeout);
      resolve(window.pywebview?.api || null);
    }, { once: true });
  });
}

async function initializeAudioSettings() {
  desktopApi = await waitForDesktopApi();
  if (!desktopApi) {
    inputDeviceSelect.replaceChildren(new Option('Запустите окно приложения Neo', 'system-default'));
    outputDeviceSelect.replaceChildren(new Option('Запустите окно приложения Neo', 'system-default'));
    inputDeviceSelect.disabled = true;
    outputDeviceSelect.disabled = true;
    micButton.disabled = true;
    autostartSwitch.disabled = true;
    wakeSwitch.disabled = true;
    micButton.title = 'Запись доступна в настольном приложении Neo';
    micLabel.textContent = 'Голос доступен в окне приложения';
    settingsFeedback.textContent = 'Веб-браузер не запрашивает доступ к микрофону. Запустите desktop-приложение Neo для голосового ввода.';
    return;
  }
  micButton.disabled = false;
  try {
    const devices = await desktopApi.get_audio_devices();
    fillDeviceSelect(inputDeviceSelect, devices.inputs, devices.default_input, devices.selected_input, 'Микрофон Windows по умолчанию');
    fillDeviceSelect(outputDeviceSelect, devices.outputs, devices.default_output, devices.selected_output, 'Динамик Windows по умолчанию');
    volumeSlider.value = String(Math.round((devices.volume ?? 1) * 100));
    volumeValue.value = `${volumeSlider.value}%`;
    volumeValue.textContent = `${volumeSlider.value}%`;
    micLabel.textContent = 'Нажмите, чтобы говорить';
    settingsFeedback.textContent = 'По умолчанию используются устройства, выбранные в Windows.';
    wakeSwitch.checked = await desktopApi.get_wake_enabled();
    wakeSwitch.disabled = false;
    const wakeState = await desktopApi.set_wake_enabled(wakeSwitch.checked);
    wakeFeedback.textContent = wakeState.listening
      ? 'Фоновый микрофон активен. Скажите «Привет Нео».'
      : 'Запускаю фоновое распознавание…';
    if (wakeSwitch.checked) setStatus('Фоновый микрофон включён. Скажите «Привет Нео».');
  } catch (error) {
    inputDeviceSelect.replaceChildren(new Option('Устройства недоступны', 'system-default'));
    outputDeviceSelect.replaceChildren(new Option('Устройства недоступны', 'system-default'));
    inputDeviceSelect.disabled = true;
    outputDeviceSelect.disabled = true;
    settingsFeedback.textContent = error.message || 'Не удалось получить список аудиоустройств.';
    micButton.disabled = true;
  }
}

wakeSwitch.addEventListener('change', async () => {
  if (!desktopApi) return;
  wakeSwitch.disabled = true;
  wakeFeedback.textContent = wakeSwitch.checked ? 'Запускаю фоновое распознавание…' : 'Останавливаю фоновый микрофон…';
  try {
    const state = await desktopApi.set_wake_enabled(wakeSwitch.checked);
    wakeFeedback.textContent = state.listening
      ? 'Фоновый микрофон активен. Скажите «Привет Нео».'
      : wakeSwitch.checked ? (state.error || 'Не удалось запустить прослушивание.') : 'Фоновое прослушивание выключено.';
  } catch (error) {
    wakeSwitch.checked = !wakeSwitch.checked;
    wakeFeedback.textContent = error.message || 'Не удалось изменить фоновое прослушивание.';
  } finally {
    wakeSwitch.disabled = false;
  }
});

window.addEventListener('neo-wake-state', (event) => {
  const state = event.detail || {};
  backgroundWakeState = state;
  updateOperationCancelButton();
  const listeningForCommand = Boolean(state.awaiting_command);
  micButton.classList.toggle('is-wake-listening', listeningForCommand);
  if (listeningForCommand) {
    micLabel.textContent = state.phase === 'wake_detected' ? 'Фраза услышана…' : 'Нео слушает — говорите';
    micButton.setAttribute('aria-label', 'Нео слушает команду после фразы активации');
  } else if (!isRecording) {
    micLabel.textContent = 'Нажмите, чтобы говорить';
    micButton.setAttribute('aria-label', 'Начать запись');
  }
  if (state.enabled && state.error) wakeFeedback.textContent = `Ошибка фонового микрофона: ${state.error}`;
  else if (state.phase === 'downloading_wake_model') wakeFeedback.textContent = 'Загружаю модель распознавания (один раз)…';
  else if (state.phase === 'loading_wake_model') wakeFeedback.textContent = 'Загружаю локальную модель распознавания…';
  else if (state.enabled && state.recognizing) wakeFeedback.textContent = 'Распознаю речь локально…';
  else if (state.enabled && state.listening) wakeFeedback.textContent = 'Фоновый микрофон активен. Скажите «Привет Нео».';
  else if (!state.enabled) wakeFeedback.textContent = 'Фоновое прослушивание выключено.';

  if (state.phase === 'thinking' && !state.last_response) lastWakeResponse = null;
  if (!state.awaiting_command && state.last_response && state.last_response !== lastWakeResponse) {
    lastWakeResponse = state.last_response;
    showReply(state.last_response);
    monitorSpeech(Boolean(state.speech_queued));
    void loadMonthEvents();
    void loadSidebar();
  }

  switch (state.phase) {
    case 'wake_detected':
      setStatus('Фраза активации услышана. Готовлю микрофон…', true);
      break;
    case 'listening_command':
      setStatus('Нео слушает вашу команду…');
      break;
    case 'listening_followup':
      setStatus('Нео ждёт ответ на уточнение. Говорите…');
      break;
    case 'transcribing':
      setStatus('Распознаю вашу команду…', true);
      break;
    case 'thinking':
      setStatus('Нео думает над ответом…', true);
      break;
    case 'speaking':
      setStatus('Нео говорит…');
      break;
    case 'responded':
      setStatus(state.needs_followup ? 'Нужно ваше уточнение…' : 'Команда обработана. Скажите «Привет Нео», если нужна ещё помощь.');
      break;
    case 'cancelled':
      setStatus('Обработка отменена.');
      break;
    case 'listening_wake':
      setStatus('Фоновый микрофон включён. Скажите «Привет Нео».');
      break;
    case 'error':
      if (state.last_response) setStatus('Не удалось обработать команду.');
      else if (state.enabled) setStatus(`Фоновый голосовой режим недоступен: ${state.error || 'неизвестная ошибка'}`);
      break;
    case 'recognizing_wake':
      setStatus('Проверяю фразу активации…');
      break;
    case 'downloading_wake_model':
      setStatus('Первый запуск: загружаю компактную модель русского распознавания…', true);
      break;
    case 'loading_wake_model':
      setStatus('Загружаю локальную модель фонового распознавания…', true);
      break;
  }
});

function fillDeviceSelect(select, devices, defaultId, selectedId, defaultLabel) {
  select.replaceChildren();
  const defaultDevice = devices.find((device) => device.id === defaultId);
  select.add(new Option(defaultDevice ? `${defaultLabel} · ${defaultDevice.name}` : defaultLabel, 'system-default'));
  for (const device of devices) {
    const suffix = device.host_api ? ` · ${device.host_api}` : '';
    const option = new Option(`${device.name}${suffix}`, device.id);
    if (device.system_default) option.textContent += ' (Windows по умолчанию)';
    select.add(option);
  }
  select.value = selectedId && devices.some((device) => device.id === selectedId) ? selectedId : 'system-default';
  select.disabled = devices.length === 0;
}

async function changeDevice(kind, value) {
  if (!desktopApi) return;
  try {
    const settings = await desktopApi.set_audio_device(kind, value === 'system-default' ? null : value);
    settingsFeedback.textContent = kind === 'input' ? 'Микрофон сохранён.' : 'Динамик сохранён.';
    fillDeviceSelect(inputDeviceSelect, settings.inputs, settings.default_input, settings.selected_input, 'Микрофон Windows по умолчанию');
    fillDeviceSelect(outputDeviceSelect, settings.outputs, settings.default_output, settings.selected_output, 'Динамик Windows по умолчанию');
  } catch (error) {
    settingsFeedback.textContent = error.message || 'Не удалось сохранить устройство.';
    await initializeAudioSettings();
  }
}

inputDeviceSelect.addEventListener('change', () => changeDevice('input', inputDeviceSelect.value));
outputDeviceSelect.addEventListener('change', () => changeDevice('output', outputDeviceSelect.value));
volumeSlider.addEventListener('input', () => {
  volumeValue.value = `${volumeSlider.value}%`;
  volumeValue.textContent = `${volumeSlider.value}%`;
});
volumeSlider.addEventListener('change', async () => {
  if (!desktopApi) return;
  try {
    await desktopApi.set_output_volume(Number(volumeSlider.value) / 100);
    settingsFeedback.textContent = 'Громкость ответа сохранена.';
  } catch (error) {
    settingsFeedback.textContent = error.message || 'Не удалось сохранить громкость.';
  }
});

async function submitCommand(text) {
  const command = text.trim();
  if (!command || activeRequest) return;
  activeRequest = true;
  activeRequestId = globalThis.crypto?.randomUUID?.() || 'neo-request-' + Date.now();
  activeRequestController = new AbortController();
  updateOperationCancelButton();
  await stopCurrentSpeech();
  showReply('');
  setStatus('Нео обрабатывает команду…', true);
  commandInput.disabled = true;
  try {
    const result = await sendText(command, activeRequestController.signal, activeRequestId);
    showReply(result.response_text);
    monitorSpeech(result.speech_queued);
    if (!result.speech_queued) setStatus(result.action === 'clarify' ? 'Нео ждёт уточнение' : 'Готово');
    await loadMonthEvents();
    void loadSidebar();
  } catch (error) {
    if (error.name === 'AbortError') {
      setStatus('Обработка отменена.');
      showReply('Команда отменена.');
      return;
    }
    setStatus('Не удалось выполнить команду');
    showReply(error.message || 'Проверьте, что локальный сервер Neo запущен.');
  } finally {
    activeRequest = false;
    activeRequestController = null;
    activeRequestId = null;
    updateOperationCancelButton();
    commandInput.disabled = false;
    commandInput.focus();
  }
}

commandForm.addEventListener('submit', (event) => {
  event.preventDefault();
  const value = commandInput.value;
  if (!value.trim()) return;
  commandInput.value = '';
  submitCommand(value);
});

function base64ToWav(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: 'audio/wav' });
}

async function toggleRecording() {
  if (!desktopApi) {
    setStatus('Запись доступна только в настольном приложении Neo');
    return;
  }
  if (activeRequest) return;
  let resumeWakeAfterRecording = false;
  try {
    if (!isRecording) {
      const wakeStatus = wakeSwitch.checked
        ? await desktopApi.get_wake_status()
        : backgroundWakeState;
      if (wakeStatus?.awaiting_command) {
        const result = await desktopApi.finish_wake_command();
        if (result.requested) {
          micLabel.textContent = 'Обрабатываю запись…';
          micButton.setAttribute('aria-label', 'Обрабатываю записанную команду');
          setStatus('Завершаю запись и обрабатываю команду…', true);
          return;
        }
      }
      await stopCurrentSpeech();
      const result = await desktopApi.start_recording();
      isRecording = true;
      micButton.classList.add('is-recording');
      micButton.setAttribute('aria-label', 'Остановить запись и отправить');
      micLabel.textContent = 'Запись идёт · нажмите, чтобы отправить';
      setStatus(`Слушаю: ${result.device}. Нажмите ещё раз, чтобы отправить запись.`);
      return;
    }
    activeRequest = true;
    activeRequestId = globalThis.crypto?.randomUUID?.() || 'neo-request-' + Date.now();
    activeRequestController = new AbortController();
    updateOperationCancelButton();
    resumeWakeAfterRecording = true;
    setStatus('Распознаю речь и обрабатываю команду…', true);
    isRecording = false;
    micButton.classList.remove('is-recording');
    micButton.setAttribute('aria-label', 'Начать запись');
    micLabel.textContent = 'Нажмите, чтобы говорить';
    const result = await desktopApi.stop_recording();
    const response = await sendVoice(base64ToWav(result.audio_base64), activeRequestController.signal, activeRequestId);
    if (response.action === 'cancel') {
      setStatus('Обработка отменена.');
      showReply('Команда отменена.');
      return;
    }
    showReply(response.response_text);
    monitorSpeech(response.speech_queued);
    if (!response.speech_queued) setStatus(response.transcription ? `Распознано: «${response.transcription}»` : 'Готово');
    await loadMonthEvents();
    void loadSidebar();
  } catch (error) {
    if (error.name === 'AbortError') {
      isRecording = false;
      micButton.classList.remove('is-recording');
      micButton.setAttribute('aria-label', 'Начать запись');
      micLabel.textContent = 'Нажмите, чтобы говорить';
      setStatus('Обработка отменена.');
      showReply('Команда отменена.');
      return;
    }
    isRecording = false;
    micButton.classList.remove('is-recording');
    micButton.setAttribute('aria-label', 'Начать запись');
    micLabel.textContent = 'Нажмите, чтобы говорить';
    setStatus('Не удалось записать или обработать команду');
    showReply(error.message || 'Проверьте выбранный микрофон и доступность голосовых моделей.');
  } finally {
    activeRequest = false;
    activeRequestController = null;
    activeRequestId = null;
    updateOperationCancelButton();
    if (resumeWakeAfterRecording && desktopApi) {
      try { await desktopApi.resume_wake_listener(); } catch (error) { console.warn('Фоновый микрофон не перезапустился:', error); }
    }
  }
}
micButton.addEventListener('click', toggleRecording);

// ===== Обработчики настроек и кнопок боковой панели =====

if (tasksToggleCompleted) {
  tasksToggleCompleted.addEventListener('click', () => {
    showCompletedTasks = !showCompletedTasks;
    tasksToggleCompleted.setAttribute('aria-pressed', String(showCompletedTasks));
    renderTasks();
  });
}

if (reminderDaysInput) {
  reminderDaysInput.addEventListener('input', () => {
    if (reminderDaysValue) {
      reminderDaysValue.textContent = reminderDaysInput.value || String(appSettings.reminder_days_ahead);
    }
  });
  reminderDaysInput.addEventListener('change', async () => {
    const value = parseInt(reminderDaysInput.value, 10);
    if (!Number.isFinite(value) || value < 1) {
      reminderDaysInput.value = String(appSettings.reminder_days_ahead);
      if (reminderDaysValue) reminderDaysValue.textContent = String(appSettings.reminder_days_ahead);
      return;
    }
    try {
      const updated = await updateSettings({ reminder_days_ahead: value });
      if (updated && typeof updated.reminder_days_ahead === 'number') {
        appSettings = { ...appSettings, ...updated };
      }
      reminderDaysInput.value = String(appSettings.reminder_days_ahead);
      if (reminderDaysValue) reminderDaysValue.textContent = String(appSettings.reminder_days_ahead);
      if (reminderDaysFeedback) {
        reminderDaysFeedback.textContent = `Напоминания показываются на ${appSettings.reminder_days_ahead} дн. вперёд.`;
      }
      await loadSidebar();
    } catch (error) {
      if (reminderDaysFeedback) {
        reminderDaysFeedback.textContent = error.message || 'Не удалось сохранить настройку.';
      }
    }
  });
}

function monthBoundaryIso(date) {
  const local = new Date(date.getFullYear(), date.getMonth(), 1);
  const offset = -local.getTimezoneOffset();
  const sign = offset >= 0 ? '+' : '-';
  const pad = (value) => String(Math.abs(value)).padStart(2, '0');
  return `${local.getFullYear()}-${pad(local.getMonth() + 1)}-01T00:00:00${sign}${pad(Math.trunc(offset / 60))}:${pad(offset % 60)}`;
}

function updateCalendarTitle() {
  calendarHeading.textContent = `${months[visibleMonth.getMonth()]} ${visibleMonth.getFullYear()}`;
}

async function loadMonthEvents() {
  const requestId = ++calendarLoadRequest;
  updateCalendarTitle();
  eventsCount.textContent = '';
  eventsList.replaceChildren(Object.assign(document.createElement('p'), { className: 'events-empty', textContent: 'Загружаю события…' }));
  try {
    const nextMonth = new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() + 1, 1);
    const events = await getEvents(monthBoundaryIso(visibleMonth), monthBoundaryIso(nextMonth));
    if (requestId !== calendarLoadRequest) return;
    monthEvents = events;
    renderCalendarGrid();
    renderSelectedDay();
  } catch (error) {
    if (requestId !== calendarLoadRequest) return;
    monthEvents = [];
    calendarGrid.replaceChildren();
    eventsList.replaceChildren(Object.assign(document.createElement('p'), { className: 'events-empty events-error', textContent: error.message || 'Не удалось загрузить события.' }));
  }
}

function dateKey(date) {
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function eventDateKey(event) {
  return event.datetime_iso ? dateKey(new Date(event.datetime_iso)) : null;
}

function renderCalendarGrid() {
  const year = visibleMonth.getFullYear();
  const month = visibleMonth.getMonth();
  const leadingDays = (new Date(year, month, 1).getDay() + 6) % 7;
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const eventCounts = new Map();
  for (const event of monthEvents) {
    const key = eventDateKey(event);
    if (key) eventCounts.set(key, (eventCounts.get(key) || 0) + 1);
  }
  const todayKey = dateKey(new Date());
  calendarGrid.replaceChildren();
  const cellCount = Math.ceil((leadingDays + daysInMonth) / 7) * 7;
  for (let cell = 0; cell < cellCount; cell += 1) {
    const dayNumber = cell - leadingDays + 1;
    if (dayNumber < 1 || dayNumber > daysInMonth) {
      const blank = document.createElement('span');
      blank.className = 'calendar-blank';
      blank.setAttribute('role', 'presentation');
      calendarGrid.append(blank);
      continue;
    }
    const day = new Date(year, month, dayNumber);
    const key = dateKey(day);
    const count = eventCounts.get(key) || 0;
    const cellButton = document.createElement('button');
    cellButton.type = 'button';
    cellButton.className = 'calendar-day';
    cellButton.style.setProperty('--cell-index', String(cell));
    cellButton.setAttribute('role', 'gridcell');
    cellButton.setAttribute('aria-selected', String(key === dateKey(selectedDate)));
    cellButton.setAttribute('aria-label', `${day.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })}${count ? `, событий: ${count}` : ''}${key === todayKey ? ', сегодня' : ''}`);
    if (key === todayKey) cellButton.classList.add('is-today');
    if (key === dateKey(selectedDate)) cellButton.classList.add('is-selected');
    if (count) cellButton.classList.add('has-events');
    const number = document.createElement('span');
    number.className = 'day-number';
    number.textContent = String(dayNumber);
    cellButton.append(number);
    if (count) {
      const marker = document.createElement('span');
      marker.className = 'day-event-count';
      marker.textContent = String(count);
      cellButton.append(marker);
    }
    cellButton.addEventListener('click', () => {
      calendarGrid.querySelector('.calendar-day.is-selected')?.classList.remove('is-selected');
      calendarGrid.querySelector('.calendar-day[aria-selected="true"]')?.setAttribute('aria-selected', 'false');
      selectedDate = day;
      cellButton.classList.add('is-selected');
      cellButton.setAttribute('aria-selected', 'true');
      renderSelectedDay();
    });
    calendarGrid.append(cellButton);
  }
}

function renderSelectedDay() {
  selectedDayHeading.textContent = selectedDate.toLocaleDateString('ru-RU', {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  });
  const dayEvents = monthEvents.filter((event) => eventDateKey(event) === dateKey(selectedDate));
  eventsCount.textContent = dayEvents.length ? `Событий: ${dayEvents.length}` : 'Событий нет';
  eventsList.replaceChildren();
  if (!dayEvents.length) {
    eventsList.append(Object.assign(document.createElement('p'), {
      className: 'events-empty', textContent: 'На этот день событий нет.',
    }));
    return;
  }
  dayEvents.forEach((item, index) => {
    const row = document.createElement('article');
    row.className = `event-row event-${item.event_type}`;
    row.style.setProperty('--row-index', String(index));
    const when = document.createElement('time');
    when.textContent = item.datetime_iso
      ? new Date(item.datetime_iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
      : 'Без времени';
    const content = document.createElement('span');
    content.textContent = item.content;
    const kind = document.createElement('small');
    kind.textContent = ({ task: 'Задача', meeting: 'Встреча', reminder: 'Напоминание' })[item.event_type] || 'Событие';
    const editButton = document.createElement('button');
    editButton.className = 'event-edit';
    editButton.type = 'button';
    editButton.textContent = 'Изменить';
    editButton.setAttribute('aria-label', `Изменить событие: ${item.content}`);
    editButton.addEventListener('click', () => openEventEditor(item, editButton));
    row.append(when, content, kind, editButton);
    eventsList.append(row);
  });
}

function toLocalDateTimeInput(value) {
  if (!value) return '';
  const date = new Date(value);
  const pad = (part) => String(part).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function openEventEditor(event, trigger) {
  editingEvent = event;
  eventEditTrigger = trigger;
  editorContent.value = event.content;
  editorDateTime.value = toLocalDateTimeInput(event.datetime_iso);
  editorType.value = event.event_type;
  editorFeedback.textContent = '';
  deleteFeedback.textContent = '';
  eventEditorForm.hidden = false;
  deleteConfirmPanel.hidden = true;
  eventEditorOverlay.hidden = false;
  editorContent.focus();
}

function closeEventEditor(restoreFocus = true) {
  eventEditorOverlay.hidden = true;
  eventEditorForm.hidden = false;
  deleteConfirmPanel.hidden = true;
  editingEvent = null;
  if (restoreFocus && eventEditTrigger?.isConnected) eventEditTrigger.focus();
  eventEditTrigger = null;
}

eventEditorForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!editingEvent) return;
  const saveButton = document.querySelector('.js-event-editor-save');
  saveButton.disabled = true;
  editorFeedback.textContent = 'Сохраняю…';
  const newDateTime = editorDateTime.value ? new Date(editorDateTime.value) : null;
  const payload = {
    content: editorContent.value.trim(),
    event_type: editorType.value,
    datetime_iso: newDateTime ? newDateTime.toISOString() : null,
  };
  try {
    await updateEvent(editingEvent.id, payload);
    if (newDateTime) {
      visibleMonth = new Date(newDateTime.getFullYear(), newDateTime.getMonth(), 1);
      selectedDate = new Date(newDateTime.getFullYear(), newDateTime.getMonth(), newDateTime.getDate());
    }
    setStatus('Изменения сохранены');
    closeEventEditor(false);
    await loadMonthEvents();
    void loadSidebar();
    calendarClose.focus();
  } catch (error) {
    editorFeedback.textContent = error.message || 'Не удалось сохранить изменения.';
  } finally {
    saveButton.disabled = false;
  }
});

document.querySelector('.js-event-editor-close').addEventListener('click', () => closeEventEditor());
document.querySelector('.js-event-editor-cancel').addEventListener('click', () => closeEventEditor());
eventEditorOverlay.addEventListener('click', (event) => {
  if (event.target === eventEditorOverlay) closeEventEditor();
});

document.querySelector('.js-event-delete').addEventListener('click', () => {
  if (!editingEvent) return;
  eventEditorForm.hidden = true;
  deleteConfirmPanel.hidden = false;
  deleteFeedback.textContent = '';
  document.querySelector('.js-delete-cancel').focus();
});
document.querySelector('.js-delete-cancel').addEventListener('click', () => {
  deleteConfirmPanel.hidden = true;
  eventEditorForm.hidden = false;
  editorContent.focus();
});
document.querySelector('.js-delete-confirm-button').addEventListener('click', async (event) => {
  if (!editingEvent) return;
  const button = event.currentTarget;
  button.disabled = true;
  deleteFeedback.textContent = 'Удаляю…';
  try {
    await deleteEvent(editingEvent.id);
    setStatus('Событие удалено');
    closeEventEditor(false);
    await loadMonthEvents();
    void loadSidebar();
    calendarClose.focus();
  } catch (error) {
    deleteFeedback.textContent = error.message || 'Не удалось удалить событие.';
  } finally {
    button.disabled = false;
  }
});

const calendarToggle = document.querySelector('.js-calendar-toggle');
const calendarClose = document.querySelector('.js-calendar-close');
function showScreen(screen) {
  const pendingTimer = screenHideTimers.get(screen);
  if (pendingTimer) window.clearTimeout(pendingTimer);
  screenHideTimers.delete(screen);
  screen.classList.remove('is-leaving', 'is-entering');
  screen.hidden = false;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  void screen.offsetWidth;
  screen.classList.add('is-entering');
  const timer = window.setTimeout(() => {
    screen.classList.remove('is-entering');
    screenHideTimers.delete(screen);
  }, 340);
  screenHideTimers.set(screen, timer);
}
function hideScreen(screen) {
  const pendingTimer = screenHideTimers.get(screen);
  if (pendingTimer) window.clearTimeout(pendingTimer);
  screenHideTimers.delete(screen);
  screen.classList.remove('is-entering');
  screen.classList.add('is-leaving');
  const duration = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 320;
  const timer = window.setTimeout(() => {
    if (screen.classList.contains('is-leaving')) screen.hidden = true;
    screen.classList.remove('is-leaving');
    screenHideTimers.delete(screen);
  }, duration);
  screenHideTimers.set(screen, timer);
}
calendarToggle.addEventListener('click', () => {
  if (sidebar) sidebar.hidden = true;
  showScreen(calendarView);
  hideScreen(assistantPanel);
  calendarToggle.setAttribute('aria-expanded', 'true');
  loadMonthEvents();
});
calendarClose.addEventListener('click', closeCalendar);
function closeCalendar() {
  if (sidebar) sidebar.hidden = false;
  hideScreen(calendarView);
  showScreen(assistantPanel);
  calendarToggle.setAttribute('aria-expanded', 'false');
  void loadSidebar();
}
document.querySelector('.js-month-prev').addEventListener('click', () => {
  visibleMonth = new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() - 1, 1);
  selectedDate = new Date(visibleMonth);
  loadMonthEvents();
});
document.querySelector('.js-month-next').addEventListener('click', () => {
  visibleMonth = new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() + 1, 1);
  selectedDate = new Date(visibleMonth);
  loadMonthEvents();
});

const settingsToggle = document.querySelector('.js-settings-toggle');
settingsToggle.addEventListener('click', () => {
  settingsOverlay.hidden = false;
  document.querySelector('.js-settings-close').focus();
});
function closeSettings() { settingsOverlay.hidden = true; settingsToggle.focus(); }
document.querySelector('.js-settings-close').addEventListener('click', closeSettings);
settingsOverlay.addEventListener('click', (event) => { if (event.target === settingsOverlay) closeSettings(); });

document.querySelectorAll('.js-settings-tab').forEach((tab) => {
  tab.addEventListener('click', async () => {
    document.querySelectorAll('.js-settings-tab').forEach((item) => {
      const active = item === tab;
      item.classList.toggle('is-active', active);
      if (active) item.setAttribute('aria-current', 'page');
      else item.removeAttribute('aria-current');
    });
    document.querySelectorAll('.settings-tab-panel').forEach((panel) => {
      panel.hidden = panel.dataset.panel !== tab.dataset.tab;
    });
    if (tab.dataset.tab === 'other') await loadAutostartSetting();
  });
});

async function loadAutostartSetting() {
  if (!desktopApi) {
    autostartSwitch.disabled = true;
    autostartFeedback.textContent = 'Автозапуск можно настроить в настольном приложении.';
    return;
  }
  autostartSwitch.disabled = true;
  try {
    const enabled = await desktopApi.get_autostart();
    autostartSwitch.checked = Boolean(enabled);
    autostartFeedback.textContent = enabled ? 'Neo запускается вместе с Windows.' : 'Автозапуск выключен.';
  } catch (error) {
    autostartFeedback.textContent = error.message || 'Не удалось проверить автозапуск.';
  } finally {
    autostartSwitch.disabled = false;
  }
}

autostartSwitch.addEventListener('change', async () => {
  if (!desktopApi) return;
  const requested = autostartSwitch.checked;
  autostartSwitch.disabled = true;
  try {
    const enabled = await desktopApi.set_autostart(requested);
    autostartSwitch.checked = Boolean(enabled);
    autostartFeedback.textContent = enabled ? 'Автозапуск включён.' : 'Автозапуск выключен.';
  } catch (error) {
    autostartSwitch.checked = !requested;
    autostartFeedback.textContent = error.message || 'Не удалось изменить автозапуск.';
  } finally {
    autostartSwitch.disabled = false;
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    if (!eventEditorOverlay.hidden) closeEventEditor();
    else if (!settingsOverlay.hidden) closeSettings();
    else if (!calendarView.hidden) closeCalendar();
  }
});

// ===== Инициализация =====
loadMonthEvents();
initializeAudioSettings();
void (async () => {
  await loadAppSettings();
  await loadSidebar();
  window.setInterval(tickCountdowns, 1000);
  // Раз в 5 минут пересобираем списки: напоминания могут «въезжать» в окно,
  // задачи — добавляться из фонового wake-word-канала.
  sidebarRefreshHandle = window.setInterval(() => { void loadSidebar(); }, 5 * 60 * 1000);
})();
