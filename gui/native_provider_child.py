"""Native Gemini transport inside the upstream killable, hardened provider child."""
import json
import httpx
from deepseek_mcp import provider_child as upstream
from api_protocol import is_gemini,gemini_request,gemini_response,GEMINI_URL

original=upstream.execute_request


def execute(settings,messages,tools,request_timeout):
    if not is_gemini(settings['base_url']):return original(settings,messages,tools,request_timeout)
    try:
        with httpx.Client(timeout=upstream._bounded_timeout(request_timeout)) as client:
            with client.stream('POST',GEMINI_URL,headers={'x-goog-api-key':settings['credential'],'Accept-Encoding':'identity'},
                json=gemini_request(settings['model'],messages,tools,settings['max_output_tokens'])) as response:
                if response.is_error:
                    return {'kind':'error','summary':'category=api status='+str(response.status_code),
                        'retryable':response.status_code==429 or response.status_code>=500}
                chunks=[];size=0
                for chunk in response.iter_bytes():
                    size+=len(chunk)
                    if size>upstream.MAX_API_RESPONSE_BYTES:raise ValueError('Response too large')
                    chunks.append(chunk)
                data=gemini_response(json.loads(b''.join(chunks)))
                return {'kind':'ok','response':data}
    except httpx.TransportError:return {'kind':'error','summary':'category=connection','retryable':True}
    except Exception:return {'kind':'error','summary':'category=client','retryable':False}


upstream.execute_request=execute
if __name__=='__main__':upstream.main()
