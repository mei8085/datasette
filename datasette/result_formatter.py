import asyncio
import csv
import hashlib
import sys

from datasette.utils import (
    add_cors_headers,
    await_me_maybe,
    call_with_supported_arguments,
    EscapeHtmlWriter,
    LimitedWriter,
    path_from_row_pks,
    path_with_format,
)
from datasette.utils.asgi import (
    AsgiStream,
    BadRequest,
    NotFound,
    Request,
    Response,
)


async def stream_csv(
    datasette,
    fetch_data,
    request,
    database,
):
    kwargs = {}
    stream = request.args.get("_stream")
    extra_parameters = [
        "{}=1".format(key)
        for key in ("_nofacet", "_nocount")
        if not request.args.get(key)
    ]
    if extra_parameters:
        if not request.query_string:
            new_query_string = "&".join(extra_parameters)
        else:
            new_query_string = request.query_string + "&" + "&".join(extra_parameters)
        new_scope = dict(request.scope, query_string=new_query_string.encode("latin-1"))
        receive = request.receive
        request = Request(new_scope, receive)
    if stream:
        if not datasette.setting("allow_csv_stream"):
            raise BadRequest("CSV streaming is disabled")
        if request.args.get("_next"):
            raise BadRequest("_next not allowed for CSV streaming")
        kwargs["_size"] = "max"

    response_or_template_contexts = await fetch_data(request)
    if isinstance(response_or_template_contexts, Response):
        return response_or_template_contexts
    elif len(response_or_template_contexts) == 4:
        data, _, _, _ = response_or_template_contexts
    else:
        data, _, _ = response_or_template_contexts

    headings = data["columns"]
    expanded_columns = set(data.get("expanded_columns") or [])
    if expanded_columns:
        headings = []
        for column in data["columns"]:
            headings.append(column)
            if column in expanded_columns:
                headings.append(f"{column}_label")

    content_type = "text/plain; charset=utf-8"
    preamble = ""
    postamble = ""

    trace = request.args.get("_trace")
    if trace:
        content_type = "text/html; charset=utf-8"
        preamble = (
            "<html><head><title>CSV debug</title></head>"
            '<body><textarea style="width: 90%; height: 70vh">'
        )
        postamble = "</textarea></body></html>"

    async def stream_fn(r):
        nonlocal data, trace
        limited_writer = LimitedWriter(r, datasette.setting("max_csv_mb"))
        if trace:
            await limited_writer.write(preamble)
            writer = csv.writer(EscapeHtmlWriter(limited_writer))
        else:
            writer = csv.writer(limited_writer)
        first = True
        next = None
        while first or (next and stream):
            try:
                kwargs = {}
                if next:
                    kwargs["_next"] = next
                if not first:
                    data, _, _ = await fetch_data(request, **kwargs)
                if first:
                    if request.args.get("_header") != "off":
                        await writer.writerow(headings)
                    first = False
                next = data.get("next")
                for row in data["rows"]:
                    if any(isinstance(r, bytes) for r in row):
                        new_row = []
                        for column, cell in zip(headings, row):
                            if isinstance(cell, bytes):
                                if data.get("table"):
                                    pks = data.get("primary_keys") or []
                                    cell = datasette.absolute_url(
                                        request,
                                        datasette.urls.row_blob(
                                            database,
                                            data["table"],
                                            path_from_row_pks(row, pks, not pks),
                                            column,
                                        ),
                                    )
                                else:
                                    url = datasette.absolute_url(
                                        request,
                                        path_with_format(
                                            request=request,
                                            format="blob",
                                            extra_qs={
                                                "_blob_column": column,
                                                "_blob_hash": hashlib.sha256(
                                                    cell
                                                ).hexdigest(),
                                            },
                                            replace_format="csv",
                                        ),
                                    )
                                    cell = url.replace("&_nocount=1", "").replace(
                                        "&_nofacet=1", ""
                                    )
                            new_row.append(cell)
                        row = new_row
                    if not expanded_columns:
                        await writer.writerow(row)
                    else:
                        new_row = []
                        for heading, cell in zip(data["columns"], row):
                            if heading in expanded_columns:
                                if cell is None:
                                    new_row.extend(("", ""))
                                else:
                                    if not isinstance(cell, dict):
                                        new_row.extend((cell, ""))
                                    else:
                                        new_row.append(cell["value"])
                                        new_row.append(cell["label"])
                            else:
                                new_row.append(cell)
                        await writer.writerow(new_row)
            except Exception as ex:
                sys.stderr.write("Caught this error: {}\n".format(ex))
                sys.stderr.flush()
                await r.write(str(ex))
                return
        await limited_writer.write(postamble)

    headers = {}
    if datasette.cors:
        add_cors_headers(headers)
    if request.args.get("_dl", None):
        if not trace:
            content_type = "text/csv; charset=utf-8"
        disposition = 'attachment; filename="{}.csv"'.format(
            request.url_vars.get("table", database)
        )
        headers["content-disposition"] = disposition

    return AsgiStream(stream_fn, headers=headers, content_type=content_type)


async def render_response(
    datasette,
    request,
    format_,
    data,
    columns,
    rows,
    sql,
    query_name,
    database,
    table,
    view_name,
    fetch_data_fn=None,
    database_route=None,
    status_code=None,
    error=None,
    truncated=None,
):
    if format_ == "csv":
        if fetch_data_fn is None:
            raise ValueError("fetch_data_fn is required for CSV format")
        return await stream_csv(
            datasette,
            fetch_data_fn,
            request,
            database_route or database,
        )
    elif format_ in datasette.renderers.keys():
        result = call_with_supported_arguments(
            datasette.renderers[format_][0],
            datasette=datasette,
            columns=columns,
            rows=rows,
            sql=sql,
            query_name=query_name,
            database=database,
            table=table,
            request=request,
            view_name=view_name,
            truncated=truncated if truncated is not None else False,
            error=error,
            args=request.args,
            data=data,
        )
        if asyncio.iscoroutine(result):
            result = await result
        if result is None:
            raise NotFound("No data")
        if isinstance(result, dict):
            r = Response(
                body=result.get("body"),
                status=result.get("status_code", status_code or 200),
                content_type=result.get("content_type", "text/plain"),
                headers=result.get("headers"),
            )
        elif isinstance(result, Response):
            r = result
            if status_code is not None:
                r.status = status_code
        else:
            assert False, f"{result} should be dict or Response"
        return r
    else:
        raise ValueError(f"Unsupported format: {format_}")


async def get_available_renderers(
    datasette,
    request,
    data,
    columns,
    rows,
    sql,
    query_name,
    database,
    table,
    view_name,
):
    renderers = {}
    url_labels_extra = {}
    if data.get("expandable_columns"):
        url_labels_extra = {"_labels": "on"}

    for key, (_, can_render) in datasette.renderers.items():
        it_can_render = call_with_supported_arguments(
            can_render,
            datasette=datasette,
            columns=columns or [],
            rows=rows or [],
            sql=sql,
            query_name=query_name,
            database=database,
            table=table,
            request=request,
            view_name=view_name,
        )
        it_can_render = await await_me_maybe(it_can_render)
        if it_can_render:
            renderers[key] = datasette.urls.path(
                path_with_format(
                    request=request, format=key, extra_qs={**url_labels_extra}
                )
            )
    return renderers
