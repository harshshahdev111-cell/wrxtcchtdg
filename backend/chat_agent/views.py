"""Local demonstration interface for the chat and its execution trace."""

from django.shortcuts import render


def demo(request):
    """Render the chat and trace interface without exposing model credentials."""
    return render(request, "chat_agent/demo.html")
