function playTTS(base64Audio) {
  const audio = new Audio(`data:audio/wav;base64,${base64Audio}`);
  audio.play();
  return audio;
}