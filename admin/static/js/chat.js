const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
const ws = new WebSocket(`${wsProtocol}://${window.location.host}/ws/chat`);

const chatBox = document.getElementById("chat-box");
const form = document.getElementById("message-form");
const input = document.getElementById("message-input");

ws.onmessage = (event) => {
  const msg = document.createElement("p");
  msg.textContent = event.data;
  chatBox.appendChild(msg);
  chatBox.scrollTop = chatBox.scrollHeight;
};

form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (input.value.trim() !== "") {
    ws.send(input.value);
    input.value = "";
  }
});