"use strict";

const apiRoot = "/api/v1/";
let conversationId = sessionStorage.getItem("chat-agent-conversation");
let userId = sessionStorage.getItem("chat-agent-user");
let activeProposal = null;
let busy = false;
let modelMode = "demo";
let polling = false;
let renderedSnapshot = "";
const welcomeTemplate = document.querySelector(".welcome").cloneNode(true);

/** Get a named element from the demonstration page. */
function element(id) {
  return document.getElementById(id);
}

/** Call the local API and surface an actionable error for unsuccessful responses. */
async function api(path, method = "GET", body) {
  const interrupted = "The connection was interrupted or the request timed out. " +
    "Your chat is saved. Reload to check its status; if you were confirming, " +
    "retry the same confirmation to check the outcome safely.";
  let response;
  try {
    response = await fetch(apiRoot + path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error(interrupted);
  }
  let data;
  try {
    data = await response.json();
  } catch {
    const error = new Error(interrupted);
    error.status = response.status;
    throw error;
  }
  if (!response.ok) {
    const error = new Error(data.detail || JSON.stringify(data));
    error.status = response.status;
    throw error;
  }
  return data;
}

/** Create a DOM node using textContent so tool and model text never becomes markup. */
function node(tag, className, text) {
  const result = document.createElement(tag);
  result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

/** Show request progress and prevent concurrent actions in the interface. */
function setBusy(value, message) {
  busy = value;
  for (const id of ["send", "start", "message", "scenario"]) {
    element(id).disabled = value;
  }
  const confirm = element("confirm");
  if (confirm) confirm.disabled = value;
  document.querySelectorAll(".suggestion").forEach((button) => {
    button.disabled = value || modelMode !== "live";
  });
  element("send").textContent = value ? "Sending…" : "Send";
  element("notice").className = "notice";
  element("notice").textContent = message;
}

/** Display a request error without silently discarding the conversation. */
function showError(error) {
  setBusy(false, error.message);
  element("notice").classList.add("error");
}

/** Add one real saved chat message, with user/model text rendered as plain text. */
function appendMessage(role, content) {
  element("messages").querySelector(".welcome")?.remove();
  const message = node("div", "message " + role);
  message.append(node("div", "role", role === "user" ? "You" : "Chat Agent"));
  message.append(node("div", "bubble", content));
  element("messages").append(message);
  element("messages").scrollTop = element("messages").scrollHeight;
}

/** Format appointment and hold-expiry timestamps consistently in India time. */
function indiaTime(value) {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

/** Display the exact server-issued proposal and bind confirmation to its fingerprint. */
function renderProposal(proposal, providers = []) {
  activeProposal = proposal;
  const container = element("proposal");
  container.replaceChildren();
  container.hidden =
    !proposal || !["pending", "executing", "done"].includes(proposal.status);
  if (container.hidden) return;
  const details = proposal.details;
  const title =
    proposal.status === "done" ? "You're all set" : "Review your booking";
  container.append(node("h3", "", title));
  const values = node("dl", "");
  const selectedProvider = providers.find(
    (provider) => provider.id === details.provider_id,
  );
  const actionLabels = {
    book: "New booking",
    cancel: "Cancellation",
    reschedule: "Reschedule",
  };
  for (const [label, value] of [
    ["Action", actionLabels[proposal.action] || proposal.action],
    ["Provider", selectedProvider?.name || details.provider_id],
    ["When", indiaTime(details.slot)],
    ["Final price", "₹" + details.price],
    ["Area", details.area || "Existing booking"],
  ]) {
    values.append(node("dt", "", label), node("dd", "", value));
  }
  container.append(values);
  let actionLabel = "booking";
  if (proposal.action === "cancel") actionLabel = "cancellation";
  if (proposal.action === "reschedule") actionLabel = "reschedule";
  const buttonText =
    proposal.status === "done"
      ? "Check confirmation"
      : "Confirm " + actionLabel;
  const button = node("button", "", buttonText);
  button.id = "confirm";
  button.disabled = busy;
  button.addEventListener("click", confirmProposal);
  container.append(button);
}

/** Summarize actual trace data while leaving the full event available for inspection. */
function traceSummary(event) {
  const data = event.data;
  if (data.error) return data.error + " — call failed; inspect the saved outcome";
  if (event.kind === "state") return data.from + " → " + data.to;
  if (event.kind === "model") {
    const tokens = data.usage?.total_tokens;
    const usage = Number.isInteger(tokens) ? tokens + " tokens" : "usage unknown";
    const cost = data.estimated_cost
      ? "$" + Number(data.estimated_cost.amount).toFixed(8) + " USD estimated"
      : "cost unknown";
    return "Model " + (data.status || "plan") + " · " + usage + " · " + cost;
  }
  if (event.kind === "context") {
    if (data.input_tokens !== undefined) {
      return "Model input · " + data.input_tokens + "/" + data.input_limit +
        " tokens (includes framing allowance)";
    }
    return "Current facts + explicit preferences · " + data.bytes + " bytes";
  }
  if (event.kind === "approval")
    return "Explicit confirmation recorded for this exact proposal";
  if (event.kind === "outcome") return data.message;
  if (Array.isArray(data.result))
    return data.result.length + " providers compared";
  if (data.result?.reviews)
    return "Untrusted review: " + data.result.reviews.at(-1);
  if (data.result?.status) return "Authoritative status: " + data.result.status;
  if (data.result?.hold_id)
    return "Slot held for ten minutes · final quote ₹" + data.result.price;
  return data.type || "Saved workflow event";
}

/** Render persisted trace events and keep errors visible beside the chat. */
function renderTrace(events) {
  const container = element("trace");
  const openEvents = new Set(
    Array.from(container.querySelectorAll("details[open]")).map(
      (item) => item.dataset.event,
    ),
  );
  container.replaceChildren();
  events.forEach((event, index) => {
    const isError = Boolean(event.data.error) || event.kind === "error";
    const row = node(
      "article",
      "event" +
        (isError ? " error" : "") +
        (event.kind === "approval" ? " approval" : ""),
    );
    row.dataset.name = event.name;
    const heading = node("div", "event-head");
    heading.append(
      node("span", "kind", event.kind),
      node("span", "event-name", event.name.replaceAll("_", " ")),
    );
    if (event.latency_ms > 0)
      heading.append(
        node("span", "latency", Math.round(event.latency_ms) + " ms"),
      );
    row.append(heading, node("div", "event-summary", traceSummary(event)));
    const detail = node("details", "");
    detail.dataset.event = String(index);
    detail.open = openEvents.has(String(index));
    detail.append(
      node("summary", "", "Inspect event data"),
      node("pre", "", JSON.stringify(event.data, null, 2)),
    );
    row.append(detail);
    container.append(row);
  });
  container.scrollTop = container.scrollHeight;
  element("event-count").textContent = events.length + " events";
}

/** Reload the conversation, ledger, and traces from the actual Django API. */
async function refresh(background = false) {
  if (!conversationId) return;
  const requestedId = conversationId;
  const [conversation, events, bookings] = await Promise.all([
    api("conversations/" + conversationId + "/"),
    api("conversations/" + conversationId + "/traces/"),
    api("users/" + userId + "/bookings/"),
  ]);
  if (requestedId !== conversationId || (background && busy)) return;
  const snapshot = JSON.stringify([conversation, events, bookings]);
  if (background && snapshot === renderedSnapshot) return;
  renderedSnapshot = snapshot;
  element("messages").replaceChildren();
  if (conversation.messages.length === 0) {
    element("messages").append(welcomeTemplate.cloneNode(true));
  } else {
    conversation.messages.forEach((message) =>
      appendMessage(message.role, message.content),
    );
  }
  const stateLabels = {
    collecting: "Ready to help",
    searching: "Finding options",
    awaiting_approval: "Review details",
    executing: "Working…",
    completed: "Completed",
    needs_attention: "Needs attention",
    error: "Let's try again",
    limited: "Paused",
  };
  element("state").textContent = stateLabels[conversation.state] || "Ready";
  element("state").dataset.state = conversation.state;
  const searchEvent = events
    .filter((event) => event.name === "search_providers")
    .at(-1);
  const providers = Array.isArray(searchEvent?.data.result)
    ? searchEvent.data.result
    : [];
  const latestProposal = conversation.proposals.at(-1);
  renderProposal(
    conversation.state === "needs_attention" && latestProposal?.status === "done"
      ? null : latestProposal,
    providers,
  );
  renderTrace(events);
  const confirmed = bookings.filter(
    (booking) => booking.status === "confirmed",
  );
  element("booking-count").textContent =
    confirmed.length +
    " verified booking" +
    (confirmed.length === 1 ? "" : "s");
  element("session-id").textContent = "Session " + conversationId.slice(0, 8);
  element("raw-trace").href =
    apiRoot + "conversations/" + conversationId + "/traces/";
}

/** Show saved background notifications without submitting or approving an action. */
async function pollUpdates() {
  if (busy || polling || !conversationId || document.hidden) return;
  polling = true;
  try {
    await refresh(true);
  } catch {
    // Temporary connection failures can be retried on the next poll.
  } finally {
    polling = false;
  }
}

/** Start an isolated conversation with the user-selected provider failure scenario. */
async function startConversation() {
  userId = "chat-" + crypto.randomUUID().slice(0, 8);
  const conversation = await api("conversations/", "POST", {
    user_id: userId,
    scenario: element("scenario").value,
  });
  conversationId = conversation.id;
  sessionStorage.setItem("chat-agent-conversation", conversationId);
  sessionStorage.setItem("chat-agent-user", userId);
  await refresh();
}

/** Send the user's message without automatically confirming any proposal. */
async function sendMessage(event) {
  event.preventDefault();
  if (busy) return;
  const message = element("message").value.trim();
  if (!message) return;
  setBusy(true, "Thinking about your request…");
  try {
    if (!conversationId) await startConversation();
    appendMessage("user", message);
    element("message").value = "";
    const result = await api(
      "conversations/" + conversationId + "/messages/",
      "POST",
      { message },
    );
    await refresh();
    setBusy(
      false,
      result.providers?.length
        ? "Compared " +
            result.providers.length +
            " providers. Review the held quote before confirming."
        : result.message,
    );
  } catch (error) {
    showError(error);
  }
}

/** Explicitly approve only the exact proposal currently displayed to the user. */
async function confirmProposal() {
  if (busy || !activeProposal) return;
  setBusy(true, "Confirming the details and checking your booking…");
  try {
    const result = await api(
      "conversations/" + conversationId + "/confirm/",
      "POST",
      {
        proposal_id: activeProposal.id,
        fingerprint: activeProposal.fingerprint,
        confirmed: true,
      },
    );
    await refresh();
    setBusy(false, result.message);
  } catch (error) {
    showError(error);
  }
}

/** Detect real versus scripted mode and restore any previous conversation on reload. */
async function initialize() {
  try {
    const health = await api("health/");
    modelMode = health.model_backend;
    element("mode").textContent =
      modelMode === "live" ? "Live" : "Offline demo";
    element("mode").dataset.mode = modelMode;
    element("mode").classList.toggle("warning", modelMode !== "live");
    if (modelMode !== "live") {
      element("message").placeholder =
        'Scripted mode: enter JSON, e.g. {"service":"plumber"}';
      element("notice").textContent =
        "Offline mode accepts scripted JSON plans. Configure live mode for English requests.";
    }
    if (conversationId) {
      try {
        await refresh();
      } catch (error) {
        if (error.status !== 404) throw error;
        conversationId = null;
        userId = null;
        sessionStorage.removeItem("chat-agent-conversation");
        sessionStorage.removeItem("chat-agent-user");
        element("notice").textContent =
          "Start a new chat to arrange a service.";
      }
    }
    setTraceVisible(
      sessionStorage.getItem("chat-agent-show-activity") === "true",
    );
    document.querySelectorAll(".suggestion").forEach((button) => {
      button.disabled = modelMode !== "live";
    });
  } catch (error) {
    showError(error);
  }
}

/** Show optional activity details without distracting from the main conversation. */
function setTraceVisible(visible) {
  element("trace-panel").hidden = !visible;
  element("workspace").classList.toggle("trace-open", visible);
  element("toggle-trace").setAttribute("aria-expanded", String(visible));
  element("toggle-trace").textContent = visible
    ? "Hide activity"
    : "Show activity";
  sessionStorage.setItem("chat-agent-show-activity", String(visible));
}

element("composer").addEventListener("submit", sendMessage);
element("toggle-trace").addEventListener("click", () => {
  setTraceVisible(element("trace-panel").hidden);
});
element("messages").addEventListener("click", (event) => {
  const suggestion = event.target.closest("[data-message]");
  if (!suggestion || busy || modelMode !== "live") return;
  element("message").value = suggestion.dataset.message;
  element("message").focus();
});
element("message").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!busy && element("message").value.trim())
      element("composer").requestSubmit();
  }
});
element("start").addEventListener("click", async () => {
  if (busy) return;
  setBusy(true, "Starting a new conversation…");
  try {
    await startConversation();
    setBusy(false, "New conversation ready. Tell me what you need.");
  } catch (error) {
    showError(error);
  }
});
initialize();
setInterval(pollUpdates, 5000);
