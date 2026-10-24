"""Native Gemini transport inside the upstream killable, hardened provider child."""
import json
import httpx
from deepseek_mcp import provider_child as upstream
from api_protocol import is_gemini,gemini_request,gemini_response,GEMINI_URL
from vertex_protocol import is_vertex,endpoint as vertex_endpoint,request as vertex_request,response as vertex_response
from transport_log import install_sync_hooks
from executor_limits import install_output_limits

install_output_limits(provider_only=True)
install_sync_hooks()

original=upstream.execute_request


def execute(settings,messages,tools,request_timeout):
    vertex=is_vertex(settings['base_url'])
    if not is_gemini(settings['base_url']) and not vertex:return original(settings,messages,tools,request_timeout)
    try:
        with httpx.Client(timeout=upstream._bounded_timeout(request_timeout)) as client:
            url=vertex_endpoint(settings['base_url'],settings['model']) if vertex else GEMINI_URL
            from vertex_protocol import headers as vertex_headers
            headers=vertex_headers(settings['base_url'],settings['credential']) if vertex else {'x-goog-api-key':settings['credential']}
            headers['Accept-Encoding']='identity'
            effort=settings.get('reasoning_effort')
            if effort=='provider-default':effort=None
            payload=vertex_request(settings['model'],messages,tools,settings['max_output_tokens'],effort) if vertex else gemini_request(settings['model'],messages,tools,settings['max_output_tokens'])
            with client.stream('POST',url,headers=headers,json=payload) as response:
                if response.is_error:
                    return {'kind':'error','summary':'category=api status='+str(response.status_code),
                        'retryable':response.status_code==429 or response.status_code>=500}
                chunks=[];size=0
                for chunk in response.iter_bytes():
                    size+=len(chunk)
                    if size>upstream.MAX_API_RESPONSE_BYTES:raise ValueError('Response too large')
                    chunks.append(chunk)
                body=json.loads(b''.join(chunks));data=vertex_response(body) if vertex else gemini_response(body)
                return {'kind':'ok','response':data}
    except httpx.TransportError:return {'kind':'error','summary':'category=connection','retryable':True}
    except Exception:return {'kind':'error','summary':'category=client','retryable':False}


upstream.execute_request=execute
if __name__=='__main__':upstream.main()
