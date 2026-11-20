import sys
from tempfile import TemporaryDirectory
from unittest import skipIf

import httpx
from blacknoise import BlackNoise
from django.core.asgi import get_asgi_application
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings


class BrowsableAPIAssets(SimpleTestCase):
    @skipIf(sys.platform == "win32", "ASGI static serving is checked in Linux Docker")
    async def test_styles_and_scripts_are_served_with_debug_disabled(self):
        """Verify the expected invariant: styles and scripts are served with debug disabled."""
        with TemporaryDirectory() as directory:
            with override_settings(DEBUG=False, STATIC_ROOT=directory):
                call_command("collectstatic", interactive=False, verbosity=0)
                application = BlackNoise(get_asgi_application())
                application.add(directory, "/static")
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=application), base_url="http://testserver"
                ) as client:
                    for path, content_type in [
                        ("css/bootstrap.min.css", "text/css"),
                        ("js/ajax-form.js", "javascript"),
                    ]:
                        with self.subTest(path=path):
                            response = await client.get(f"/static/rest_framework/{path}")
                            self.assertEqual(response.status_code, 200)
                            self.assertIn(content_type, response.headers["Content-Type"])
                            self.assertTrue(response.content)

    def test_demo_page_loads_the_chat_and_trace_interface(self):
        """Serve the demonstration page with links to its chat and trace assets."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="Agent chat"')
        self.assertContains(response, 'aria-label="Execution trace"')
        self.assertContains(response, "/static/chat_agent/demo.css")
        self.assertContains(response, "/static/chat_agent/demo.js")
        self.assertContains(response, "Chat Agent")
        self.assertContains(response, "<title>Chat Agent</title>", html=True)
