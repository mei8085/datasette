import asyncio
import textwrap
import time
import urllib
from markupsafe import escape


from datasette.database import QueryInterrupted
from datasette.result_formatter import stream_csv, render_response, get_available_renderers
from datasette.utils import (
    add_cors_headers,
    await_me_maybe,
    InvalidSql,
    call_with_supported_arguments,
    path_with_added_args,
    path_with_removed_args,
    path_with_format,
    sqlite3,
)
from datasette.utils.asgi import (
    NotFound,
    Response,
    BadRequest,
)


class DatasetteError(Exception):
    def __init__(
        self,
        message,
        title=None,
        error_dict=None,
        status=500,
        template=None,
        message_is_html=False,
    ):
        self.message = message
        self.title = title
        self.error_dict = error_dict or {}
        self.status = status
        self.message_is_html = message_is_html


class View:
    async def head(self, request, datasette):
        if not hasattr(self, "get"):
            return await self.method_not_allowed(request)
        response = await self.get(request, datasette)
        response.body = ""
        return response

    async def method_not_allowed(self, request):
        if (
            request.path.endswith(".json")
            or request.headers.get("content-type") == "application/json"
        ):
            response = Response.json(
                {"ok": False, "error": "Method not allowed"}, status=405
            )
        else:
            response = Response.text("Method not allowed", status=405)
        return response

    async def options(self, request, datasette):
        response = Response.text("ok")
        response.headers["allow"] = ", ".join(
            method.upper()
            for method in ("head", "get", "post", "put", "patch", "delete")
            if hasattr(self, method)
        )
        return response

    async def __call__(self, request, datasette):
        try:
            handler = getattr(self, request.method.lower())
        except AttributeError:
            return await self.method_not_allowed(request)
        return await handler(request, datasette)


class BaseView:
    ds = None
    has_json_alternate = True

    def __init__(self, datasette):
        self.ds = datasette

    async def head(self, *args, **kwargs):
        response = await self.get(*args, **kwargs)
        response.body = b""
        return response

    async def method_not_allowed(self, request):
        if (
            request.path.endswith(".json")
            or request.headers.get("content-type") == "application/json"
        ):
            response = Response.json(
                {"ok": False, "error": "Method not allowed"}, status=405
            )
        else:
            response = Response.text("Method not allowed", status=405)
        return response

    async def options(self, request, *args, **kwargs):
        return Response.text("ok")

    async def get(self, request, *args, **kwargs):
        return await self.method_not_allowed(request)

    async def post(self, request, *args, **kwargs):
        return await self.method_not_allowed(request)

    async def put(self, request, *args, **kwargs):
        return await self.method_not_allowed(request)

    async def patch(self, request, *args, **kwargs):
        return await self.method_not_allowed(request)

    async def delete(self, request, *args, **kwargs):
        return await self.method_not_allowed(request)

    async def dispatch_request(self, request):
        if self.ds:
            await self.ds.refresh_schemas()
        handler = getattr(self, request.method.lower(), None)
        response = await handler(request)
        if self.ds.cors:
            add_cors_headers(response.headers)
        return response

    async def render(self, templates, request, context=None):
        context = context or {}
        environment = self.ds.get_jinja_environment(request)
        template = environment.select_template(templates)
        template_context = {
            **context,
            **{
                "select_templates": [
                    f"{'*' if template_name == template.name else ''}{template_name}"
                    for template_name in templates
                ],
            },
        }
        headers = {}
        if self.has_json_alternate:
            alternate_url_json = self.ds.absolute_url(
                request,
                self.ds.urls.path(path_with_format(request=request, format="json")),
            )
            template_context["alternate_url_json"] = alternate_url_json
            headers.update(
                {
                    "Link": '<{}>; rel="alternate"; type="application/json+datasette"'.format(
                        alternate_url_json
                    )
                }
            )
        return Response.html(
            await self.ds.render_template(
                template,
                template_context,
                request=request,
                view_name=self.name,
            ),
            headers=headers,
        )

    @classmethod
    def as_view(cls, *class_args, **class_kwargs):
        async def view(request, send):
            self = view.view_class(*class_args, **class_kwargs)
            return await self.dispatch_request(request)

        view.view_class = cls
        view.__doc__ = cls.__doc__
        view.__module__ = cls.__module__
        view.__name__ = cls.__name__
        return view


class DataView(BaseView):
    name = ""

    def redirect(self, request, path, forward_querystring=True, remove_args=None):
        if request.query_string and "?" not in path and forward_querystring:
            path = f"{path}?{request.query_string}"
        if remove_args:
            path = path_with_removed_args(request, remove_args, path=path)
        r = Response.redirect(path)
        r.headers["Link"] = f"<{path}>; rel=preload"
        if self.ds.cors:
            add_cors_headers(r.headers)
        return r

    async def data(self, request):
        raise NotImplementedError

    async def as_csv(self, request, database):
        return await stream_csv(self.ds, self.data, request, database)

    async def get(self, request):
        db = await self.ds.resolve_database(request)
        database = db.name
        database_route = db.route

        _format = request.url_vars["format"]
        data_kwargs = {}

        if _format is None:
            data_kwargs["default_labels"] = True

        extra_template_data = {}
        start = time.perf_counter()
        status_code = None
        templates = []
        try:
            response_or_template_contexts = await self.data(request, **data_kwargs)
            if isinstance(response_or_template_contexts, Response):
                return response_or_template_contexts
            if len(response_or_template_contexts) == 4:
                (
                    data,
                    extra_template_data,
                    templates,
                    status_code,
                ) = response_or_template_contexts
            else:
                data, extra_template_data, templates = response_or_template_contexts
        except QueryInterrupted as ex:
            raise DatasetteError(
                textwrap.dedent("""
                <p>SQL query took too long. The time limit is controlled by the
                <a href="https://docs.datasette.io/en/stable/settings.html#sql-time-limit-ms">sql_time_limit_ms</a>
                configuration option.</p>
                <textarea style="width: 90%">{}</textarea>
                <script>
                let ta = document.querySelector("textarea");
                ta.style.height = ta.scrollHeight + "px";
                </script>
            """.format(escape(ex.sql))).strip(),
                title="SQL Interrupted",
                status=400,
                message_is_html=True,
            )
        except (sqlite3.OperationalError, InvalidSql) as e:
            raise DatasetteError(str(e), title="Invalid SQL", status=400)

        except sqlite3.OperationalError as e:
            raise DatasetteError(str(e))

        except DatasetteError:
            raise

        end = time.perf_counter()
        data["query_ms"] = (end - start) * 1000

        if _format == "jsono":
            return self.redirect(
                request,
                path_with_added_args(
                    request,
                    {"_shape": "objects"},
                    path=request.path.rsplit(".jsono", 1)[0] + ".json",
                ),
                forward_querystring=False,
            )

        if _format == "csv":
            r = await self.as_csv(request, database_route)
        elif _format in self.ds.renderers.keys():
            r = await render_response(
                self.ds,
                request,
                _format,
                data,
                data.get("columns") or [],
                data.get("rows") or [],
                data.get("query", {}).get("sql", None),
                data.get("query_name"),
                database,
                data.get("table"),
                self.name,
                status_code=status_code,
                error=data.get("error"),
                truncated=False,
            )
        else:
            extras = {}
            if callable(extra_template_data):
                extras = extra_template_data()
                if asyncio.iscoroutine(extras):
                    extras = await extras
            else:
                extras = extra_template_data

            renderers = await get_available_renderers(
                self.ds,
                request,
                data,
                data.get("columns") or [],
                data.get("rows") or [],
                data.get("query", {}).get("sql", None),
                data.get("query_name"),
                database,
                data.get("table"),
                self.name,
            )

            url_labels_extra = {}
            if data.get("expandable_columns"):
                url_labels_extra = {"_labels": "on"}
            url_csv_args = {"_size": "max", **url_labels_extra}
            url_csv = self.ds.urls.path(
                path_with_format(request=request, format="csv", extra_qs=url_csv_args)
            )
            url_csv_path = url_csv.split("?")[0]
            context = {
                **data,
                **extras,
                **{
                    "renderers": renderers,
                    "url_csv": url_csv,
                    "url_csv_path": url_csv_path,
                    "url_csv_hidden_args": [
                        (key, value)
                        for key, value in urllib.parse.parse_qsl(request.query_string)
                        if key not in ("_labels", "_facet", "_size")
                    ]
                    + [("_size", "max")],
                    "settings": self.ds.settings_dict(),
                },
            }
            if "metadata" not in context:
                context["metadata"] = await self.ds.get_instance_metadata()
            r = await self.render(templates, request=request, context=context)
            if status_code is not None:
                r.status = status_code

        ttl = request.args.get("_ttl", None)
        if ttl is None or not ttl.isdigit():
            ttl = self.ds.setting("default_cache_ttl")

        return self.set_response_headers(r, ttl)

    def set_response_headers(self, response, ttl):
        # Set far-future cache expiry
        if self.ds.cache_headers and response.status == 200:
            ttl = int(ttl)
            if ttl == 0:
                ttl_header = "no-cache"
            else:
                ttl_header = f"max-age={ttl}"
            response.headers["Cache-Control"] = ttl_header
        response.headers["Referrer-Policy"] = "no-referrer"
        if self.ds.cors:
            add_cors_headers(response.headers)
        return response


def _error(messages, status=400):
    return Response.json({"ok": False, "errors": messages}, status=status)
