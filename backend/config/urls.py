from chat_agent.views import demo
from django.urls import include, path

urlpatterns = [
    path("", demo, name="demo"),
    path("api/v1/", include("chat_agent.api.v1.urls")),
]
