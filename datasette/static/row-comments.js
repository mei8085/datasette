var rowCommentsSidebar = null;
var currentRowInfo = null;
var csrftoken = null;

function getCsrfToken() {
  if (csrftoken) return csrftoken;
  var cookies = document.cookie.split(";");
  for (var i = 0; i < cookies.length; i++) {
    var parts = cookies[i].trim().split("=");
    if (parts[0] === "ds_csrftoken") {
      csrftoken = decodeURIComponent(parts[1] || "");
      break;
    }
  }
  return csrftoken;
}

function formatDate(dateString) {
  if (!dateString) return "";
  try {
    var date = new Date(dateString);
    if (isNaN(date.getTime())) return dateString;
    return date.toLocaleString();
  } catch (e) {
    return dateString;
  }
}

function createCommentElement(comment, currentActorId) {
  var div = document.createElement("div");
  div.className = "row-comment";
  div.dataset.commentId = comment.id;

  var header = document.createElement("div");
  header.className = "row-comment-header";

  var actorName = document.createElement("span");
  actorName.className = "row-comment-actor";
  actorName.textContent = comment.actor_name || "Anonymous";

  var dateSpan = document.createElement("span");
  dateSpan.className = "row-comment-date";
  dateSpan.textContent = formatDate(comment.created_at);

  header.appendChild(actorName);
  header.appendChild(dateSpan);

  if (currentActorId && comment.actor_id === currentActorId) {
    var deleteBtn = document.createElement("button");
    deleteBtn.className = "row-comment-delete btn btn-ghost btn-sm";
    deleteBtn.textContent = "Delete";
    deleteBtn.dataset.commentId = comment.id;
    deleteBtn.addEventListener("click", function (ev) {
      ev.preventDefault();
      deleteComment(comment.id);
    });
    header.appendChild(deleteBtn);
  }

  var body = document.createElement("div");
  body.className = "row-comment-body";
  body.textContent = comment.comment_text;

  div.appendChild(header);
  div.appendChild(body);

  return div;
}

function ensureRowCommentsSidebar() {
  if (rowCommentsSidebar) {
    return rowCommentsSidebar;
  }

  var overlay = document.createElement("div");
  overlay.className = "row-comments-overlay";
  overlay.addEventListener("click", function (ev) {
    if (ev.target === overlay) {
      closeRowCommentsSidebar();
    }
  });

  var sidebar = document.createElement("div");
  sidebar.className = "row-comments-sidebar";
  sidebar.innerHTML = `
    <div class="row-comments-header">
      <h3 class="row-comments-title">Row Comments</h3>
      <button class="row-comments-close btn btn-ghost" aria-label="Close">
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      </button>
    </div>
    <div class="row-comments-info"></div>
    <div class="row-comments-list"></div>
    <div class="row-comments-form-container">
      <div class="row-comments-form-not-logged-in" hidden>
        <p>Please <a href="/-/login">log in</a> to add a comment.</p>
      </div>
      <form class="row-comments-form">
        <textarea class="row-comments-input" placeholder="Add a comment..." rows="3"></textarea>
        <div class="row-comments-form-actions">
          <button type="submit" class="btn btn-primary row-comments-submit">Post Comment</button>
        </div>
      </form>
      <div class="row-comments-error" hidden></div>
    </div>
  `;

  overlay.appendChild(sidebar);
  document.body.appendChild(overlay);

  var closeBtn = sidebar.querySelector(".row-comments-close");
  closeBtn.addEventListener("click", function () {
    closeRowCommentsSidebar();
  });

  var form = sidebar.querySelector(".row-comments-form");
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    submitComment();
  });

  rowCommentsSidebar = {
    overlay: overlay,
    sidebar: sidebar,
    info: sidebar.querySelector(".row-comments-info"),
    list: sidebar.querySelector(".row-comments-list"),
    form: form,
    input: sidebar.querySelector(".row-comments-input"),
    error: sidebar.querySelector(".row-comments-error"),
    notLoggedIn: sidebar.querySelector(".row-comments-form-not-logged-in"),
    submitBtn: sidebar.querySelector(".row-comments-submit"),
  };

  return rowCommentsSidebar;
}

async function getCurrentActor() {
  try {
    var response = await fetch("/-/actor.json");
    if (response.ok) {
      var data = await response.json();
      return data.actor;
    }
  } catch (e) {
    console.error("Error fetching actor:", e);
  }
  return null;
}

function openRowCommentsSidebar(database, table, pkValues, rowDisplay) {
  var sidebar = ensureRowCommentsSidebar();
  currentRowInfo = {
    database: database,
    table: table,
    pkValues: pkValues,
  };

  sidebar.info.innerHTML =
    '<p class="row-comments-row-info">Row: <strong>' +
    (rowDisplay || pkValues) +
    "</strong></p>";
  sidebar.list.innerHTML = '<p class="row-comments-loading">Loading comments...</p>';
  sidebar.error.hidden = true;
  sidebar.input.value = "";
  sidebar.submitBtn.disabled = false;
  sidebar.submitBtn.textContent = "Post Comment";

  document.body.classList.add("row-comments-open");
  sidebar.overlay.classList.add("open");

  loadComments();
}

function closeRowCommentsSidebar() {
  if (rowCommentsSidebar) {
    document.body.classList.remove("row-comments-open");
    rowCommentsSidebar.overlay.classList.remove("open");
  }
  currentRowInfo = null;
}

async function loadComments() {
  if (!currentRowInfo) return;

  var sidebar = ensureRowCommentsSidebar();
  sidebar.list.innerHTML = '<p class="row-comments-loading">Loading comments...</p>';

  try {
    var url =
      "/" +
      encodeURIComponent(currentRowInfo.database) +
      "/" +
      encodeURIComponent(currentRowInfo.table) +
      "/" +
      currentRowInfo.pkValues +
      "/-/comments";
    var response = await fetch(url, {
      headers: {
        Accept: "application/json",
      },
    });
    var data = await response.json();

    if (!response.ok || data.ok === false) {
      sidebar.list.innerHTML =
        '<p class="row-comments-error-text">Error loading comments: ' +
        (data.error || "Unknown error") +
        "</p>";
      return;
    }

    var actor = await getCurrentActor();
    var actorId = actor ? actor.id : null;

    if (!actor) {
      sidebar.form.hidden = true;
      sidebar.notLoggedIn.hidden = false;
    } else {
      sidebar.form.hidden = false;
      sidebar.notLoggedIn.hidden = true;
    }

    var comments = data.comments || [];
    if (comments.length === 0) {
      sidebar.list.innerHTML =
        '<p class="row-comments-empty">No comments yet. Be the first to comment!</p>';
      return;
    }

    sidebar.list.innerHTML = "";
    comments.forEach(function (comment) {
      sidebar.list.appendChild(createCommentElement(comment, actorId));
    });
  } catch (e) {
    console.error("Error loading comments:", e);
    sidebar.list.innerHTML =
      '<p class="row-comments-error-text">Error loading comments. Please try again.</p>';
  }
}

async function submitComment() {
  if (!currentRowInfo) return;

  var sidebar = ensureRowCommentsSidebar();
  var commentText = sidebar.input.value.trim();

  if (!commentText) {
    showCommentError("Please enter a comment.");
    return;
  }

  sidebar.error.hidden = true;
  sidebar.submitBtn.disabled = true;
  sidebar.submitBtn.textContent = "Posting...";

  try {
    var url =
      "/" +
      encodeURIComponent(currentRowInfo.database) +
      "/" +
      encodeURIComponent(currentRowInfo.table) +
      "/" +
      currentRowInfo.pkValues +
      "/-/comments";

    var headers = {
      "Content-Type": "application/json",
      Accept: "application/json",
    };
    var token = getCsrfToken();
    if (token) {
      headers["x-csrftoken"] = token;
    }

    var response = await fetch(url, {
      method: "POST",
      headers: headers,
      body: JSON.stringify({ comment_text: commentText }),
    });

    var data = await response.json();

    if (!response.ok || data.ok === false) {
      showCommentError(data.error || "Failed to post comment");
      sidebar.submitBtn.disabled = false;
      sidebar.submitBtn.textContent = "Post Comment";
      return;
    }

    sidebar.input.value = "";
    sidebar.submitBtn.disabled = false;
    sidebar.submitBtn.textContent = "Post Comment";
    loadComments();
  } catch (e) {
    console.error("Error posting comment:", e);
    showCommentError("Failed to post comment. Please try again.");
    sidebar.submitBtn.disabled = false;
    sidebar.submitBtn.textContent = "Post Comment";
  }
}

async function deleteComment(commentId) {
  if (!confirm("Are you sure you want to delete this comment?")) {
    return;
  }

  try {
    var url = "/-/comments/" + commentId;
    var headers = {
      Accept: "application/json",
    };
    var token = getCsrfToken();
    if (token) {
      headers["x-csrftoken"] = token;
    }

    var response = await fetch(url, {
      method: "DELETE",
      headers: headers,
    });

    var data = await response.json();

    if (!response.ok || data.ok === false) {
      alert("Error deleting comment: " + (data.error || "Unknown error"));
      return;
    }

    loadComments();
  } catch (e) {
    console.error("Error deleting comment:", e);
    alert("Error deleting comment. Please try again.");
  }
}

function showCommentError(message) {
  var sidebar = ensureRowCommentsSidebar();
  sidebar.error.textContent = message;
  sidebar.error.hidden = false;
}

function addCommentButtons() {
  var rows = document.querySelectorAll("table.rows-and-columns tbody tr");
  var basePath = window.DATASETTE_BASE_URL || "";
  var pathParts = window.location.pathname.replace(/^\/+/, "").split("/");
  var database = pathParts[0] ? decodeURIComponent(pathParts[0]) : "";
  var table = pathParts[1] ? decodeURIComponent(pathParts[1]) : "";

  if (!database || !table) return;

  rows.forEach(function (row) {
    if (row.querySelector(".row-comment-button")) return;

    var firstCell = row.querySelector("td:first-child");
    if (!firstCell) return;

    var link = firstCell.querySelector("a");
    var pkValues = null;
    var rowDisplay = null;

    if (link) {
      var href = link.getAttribute("href");
      if (href) {
        var parts = href.split("/").filter(Boolean);
        pkValues = parts[parts.length - 1];
        rowDisplay = link.textContent.trim();
      }
    }

    if (!pkValues) {
      return;
    }

    var button = document.createElement("button");
    button.className = "row-comment-button btn btn-ghost btn-sm";
    button.innerHTML =
      '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>';
    button.setAttribute("aria-label", "View comments");
    button.title = "View comments";
    button.dataset.database = database;
    button.dataset.table = table;
    button.dataset.pkValues = pkValues;
    button.dataset.rowDisplay = rowDisplay || pkValues;

    button.addEventListener("click", function (ev) {
      ev.preventDefault();
      openRowCommentsSidebar(database, table, pkValues, rowDisplay || pkValues);
    });

    firstCell.insertBefore(button, firstCell.firstChild);
  });
}

document.addEventListener("datasette_init", function () {
  if (document.querySelector("table.rows-and-columns")) {
    addCommentButtons();
  }
});

document.addEventListener("DOMContentLoaded", function () {
  if (document.querySelector("table.rows-and-columns")) {
    addCommentButtons();
  }
});
