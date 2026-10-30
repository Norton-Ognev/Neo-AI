const API_BASE = '';

export async function sendText(text, signal, requestId) {
  return request('/api/text', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, session_id: getSessionId(), request_id: requestId, speak: true }),
    signal,
  });
}

export async function sendVoice(audioBlob, signal, requestId) {
  const formData = new FormData();
  const extension = audioBlob.type.includes('wav') ? 'wav' : audioBlob.type.includes('ogg') ? 'ogg' : 'webm';
  formData.append('audio', audioBlob, `recording.${extension}`);
  formData.append('session_id', getSessionId());
  formData.append('request_id', requestId);
  formData.append('speak', 'true');
  return request('/api/voice/command', {
    method: 'POST',
    body: formData,
    signal,
  });
}

export function cancelRequest(requestId) {
  if (!requestId) return Promise.resolve({ cancelled: false });
  return request('/api/cancel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request_id: requestId }),
  });
}

export function getEvents(start, end, includeCompleted = true) {
  const params = new URLSearchParams({ start, end, include_completed: String(includeCompleted) });
  return request(`/api/events?${params}`);
}

export function getUpcomingReminders(days) {
  const params = new URLSearchParams();
  if (days) params.set('days', String(days));
  const query = params.toString();
  return request(`/api/events/upcoming-reminders${query ? `?${query}` : ''}`);
}

export function getTasks(includeCompleted = true) {
  const params = new URLSearchParams({ include_completed: String(includeCompleted) });
  return request(`/api/events/tasks?${params}`);
}

export function getSettings() {
  return request('/api/settings');
}

export function updateSettings(payload) {
  return request('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function createEvent(event) {
  return request('/api/events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(event),
  });
}

export function updateEvent(eventId, event) {
  return request(`/api/events/${eventId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(event),
  });
}

export function setEventCompleted(eventId, isCompleted) {
  return request(`/api/events/${eventId}/completion`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_completed: Boolean(isCompleted) }),
  });
}

export function deleteEvent(eventId) {
  return request(`/api/events/${eventId}`, { method: 'DELETE' });
}

export function getVoiceStatus() {
  return request('/api/voice/status');
}

export function stopSpeech() {
  return request('/api/voice/stop', { method: 'POST' });
}

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Ошибка сервера: ${response.status}`);
  return payload;
}

function getSessionId() {
  let id = localStorage.getItem('neo-session-id');
  if (!id) {
    id = globalThis.crypto?.randomUUID?.() || `neo-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    localStorage.setItem('neo-session-id', id);
  }
  return id;
}
