import logging
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette.responses import Response
from starlette.testclient import TestClient

from asgi_correlation_id.middleware import FAILED_VALIDATION_MESSAGE
from tests.conftest import (
    TRANSFORMER_VALUE,
    default_app,
    generator_app,
    no_validator_or_transformer_app,
    transformer_app,
)

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

logger = logging.getLogger('asgi_correlation_id')

apps = [default_app, no_validator_or_transformer_app, transformer_app, generator_app]

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('app', [default_app, no_validator_or_transformer_app, generator_app])
async def test_returned_response_headers(app):
    """
    We expect:
     - our request id header to be returned back to us
     - the request id header name to be returned in access-control-expose-headers
    """

    @app.get('/test', status_code=200)
    async def test_view() -> dict:
        logger.debug('Test view')
        return {'test': 'test'}

    async with AsyncClient(app=app, base_url='http://test') as client:
        # Check we get the right headers back
        correlation_id = uuid4().hex
        response = await client.get('test', headers={'X-Request-ID': correlation_id})
        assert response.headers['access-control-expose-headers'] == 'X-Request-ID'
        assert response.headers['X-Request-ID'] == correlation_id

        # And do it one more time, jic
        second_correlation_id = uuid4().hex
        second_response = await client.get('test', headers={'X-Request-ID': second_correlation_id})
        assert second_response.headers['access-control-expose-headers'] == 'X-Request-ID'
        assert second_response.headers['X-Request-ID'] == second_correlation_id

        # Then try without specifying a request id
        third_response = await client.get('test')
        assert third_response.headers['access-control-expose-headers'] == 'X-Request-ID'
        assert third_response.headers['X-Request-ID'] not in [correlation_id, second_correlation_id]


bad_uuids = [
    'test',
    'bad-uuid',
    '1x' * 16,  # len of uuid is 32
    uuid4().hex[:-1] + 'x',
]


@pytest.mark.parametrize('value', bad_uuids)
@pytest.mark.parametrize('app', [default_app, transformer_app, generator_app])
async def test_non_uuid_header(client, caplog, value, app):
    """
    We expect the middleware to ignore our request ID and log a warning
    when the request ID we pass doesn't correspond to the uuid4 format.
    """

    @app.get('/test', status_code=200)
    async def test_view() -> dict:
        logger.debug('Test view')
        return {'test': 'test'}

    async with AsyncClient(app=app, base_url='http://test') as client:
        response = await client.get('test', headers={'X-Request-ID': value})
        assert response.headers['X-Request-ID'] != value
        assert caplog.messages[0] == FAILED_VALIDATION_MESSAGE.replace('%s', value)


@pytest.mark.parametrize('app', apps)
async def test_websocket_request(caplog, app):
    """
    We expect websocket requests to not be handled.
    This test could use improvement.
    """

    @app.websocket_route('/ws')
    async def websocket(websocket: 'WebSocket'):
        await websocket.accept()
        await websocket.send_json({'msg': 'Hello WebSocket'})
        await websocket.close()

    client = TestClient(app)
    with client.websocket_connect('/ws') as ws:
        ws.receive_json()
        assert caplog.messages == []


@pytest.mark.parametrize('app', apps)
async def test_access_control_expose_headers(caplog, app):
    """
    The middleware should add the correlation ID header name to exposed headers.
    The middleware should not overwrite other values, but should append to it.
    """

    @app.get('/access-control-expose-headers')
    async def access_control_view() -> Response:
        return Response(status_code=204, headers={'Access-Control-Expose-Headers': 'test1, test2'})

    async with AsyncClient(app=app, base_url='http://test') as client:
        response = await client.get('access-control-expose-headers')
        assert response.headers['Access-Control-Expose-Headers'] == 'test1, test2, X-Request-ID'


@pytest.mark.parametrize('app', apps)
async def test_multiple_headers_same_name(caplog, app):
    """
    The middleware should not change the headers that were set in the response and return all of them as it is.
    """

    @app.get('/multiple_headers_same_name')
    async def multiple_headers_response() -> Response:
        response = Response(status_code=204)
        response.set_cookie('access_token_cookie', 'test-access-token')
        response.set_cookie('refresh_token_cookie', 'test-refresh-token')
        return response

    async with AsyncClient(app=app, base_url='http://test') as client:
        response = await client.get('multiple_headers_same_name')
        assert response.headers['set-cookie'].find('access_token_cookie') != -1
        assert response.headers['set-cookie'].find('refresh_token_cookie') != -1


async def test_no_validator():
    async with AsyncClient(app=no_validator_or_transformer_app, base_url='http://test') as client:
        response = await client.get('test', headers={'X-Request-ID': 'bad-uuid'})
        assert response.headers['X-Request-ID'] == 'bad-uuid'


async def test_custom_transformer():
    cid = uuid4().hex
    async with AsyncClient(app=transformer_app, base_url='http://test') as client:
        response = await client.get('test', headers={'X-Request-ID': cid})
        assert response.headers['X-Request-ID'] == cid * 2


async def test_custom_generator():
    async with AsyncClient(app=generator_app, base_url='http://test') as client:
        response = await client.get('test', headers={'X-Request-ID': 'bad-uuid'})
        assert response.headers['X-Request-ID'] == TRANSFORMER_VALUE
