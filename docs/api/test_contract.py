#!/usr/bin/env python3
"""离线文档契约校验。不会启动API、访问数据库或调用任何生成服务。"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parent
URI = 'urn:omniflow:openapi:v1'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--openapi-meta', type=Path, help='已下载的官方OpenAPI3.1元schema；本脚本不会联网获取')
    args = parser.parse_args()
    doc = json.loads((ROOT/'openapi-v1.json').read_text())
    spec = importlib.util.spec_from_file_location('build_api_contract', ROOT/'build_openapi.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    assert doc == builder.DOC, '生成产物过期，请运行build_openapi.py'
    assert doc['openapi'] == '3.1.0' and doc['x-deployed'] is False
    assert doc['servers'][0]['url'] == '/api/v1'
    registry = Registry().with_resource(URI, Resource.from_contents(doc, default_specification=DRAFT202012))

    def validate(name, value):
        Draft202012Validator({'$ref': URI+'#/components/schemas/'+name},
                             registry=registry, format_checker=FormatChecker()).validate(value)

    refs = []
    def visit(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k == '$ref':
                    assert v.startswith('#/'), '契约中出现未归档的外部引用: '+v
                    target = doc
                    for part in v[2:].split('/'):
                        target = target[part.replace('~1','/').replace('~0','~')]
                    refs.append(v)
                else:
                    visit(v)
        elif isinstance(value,list):
            for child in value:
                visit(child)
    visit(doc)
    examples = 0
    for name,schema in doc['components']['schemas'].items():
        Draft202012Validator.check_schema(schema)
        for value in schema.get('examples',[]):
            validate(name,value)
            examples += 1

    ids = set()
    methods = 0
    for path, operations in doc['paths'].items():
        path_names = set(re.findall(r'\{([^}]+)\}',path))
        for method, op in operations.items():
            methods += 1
            assert op['operationId'] not in ids
            ids.add(op['operationId'])
            assert op['x-implementation-status'] == 'design-only'
            params = [doc['components']['parameters'][p['$ref'].split('/')[-1]] if '$ref' in p else p
                      for p in op.get('parameters',[])]
            assert {p['name'] for p in params if p['in']=='path'} == path_names
            assert all(p.get('required') for p in params if p['in']=='path')
            assert len({(p['in'],p['name']) for p in params}) == len(params)
            for security in op['security']:
                assert set(security) <= set(doc['components']['securitySchemes'])
            if method in ['post','patch','put','delete']:
                assert op['security'] and all('CsrfHeader' in s for s in op['security'])
            if path.startswith('/admin/'):
                assert op['x-required-role']=='admin'
                assert all('SessionCookie' in s for s in op['security'])
            if path.startswith('/media-grants/'):
                assert op['security'] == [{'MediaGrantToken':[]}]
            if method=='head':
                resolved_responses = [doc['components']['responses'][r['$ref'].split('/')[-1]] if '$ref' in r else r
                                      for r in op['responses'].values()]
                assert all('content' not in r for r in resolved_responses)
            if '204' in op['responses']:
                assert 'content' not in op['responses']['204']
            if any(p['name']=='Idempotency-Key' for p in params):
                assert '409' in op['responses'] and '410' in op['responses']
    for path in ['/tasks','/conversations','/conversations/{conversation_id}/messages','/uploads']:
        assert any(p.get('$ref','').endswith('/IdempotencyKey') for p in doc['paths'][path]['post']['parameters'])
    events = doc['paths']['/conversations/{conversation_id}/events']['get']
    assert events['security']==[{'SessionCookie':[]}]
    assert '410' in events['responses'] and 'text/event-stream' in events['responses']['200']['content']
    download=doc['paths']['/artifacts/{artifact_id}/versions/{version_id}/content']
    assert {'200','206','416'} <= set(download['get']['responses'])
    assert all(op['security']==[{'SessionCookie':[]}] for op in download.values())
    assert doc['components']['schemas']['Capabilities']['properties']['business_quotas_enabled']=={'const':False}
    for name in ['Conversation','Run','Task','ArtifactVersion','OperationalTask']:
        fields = doc['components']['schemas'][name]['properties']
        assert not {'provider_conversation_id','provider_task_id','access_token','api_key','local_path','pid'} & set(fields)
    assert 'artifact_version_ids' in doc['components']['schemas']['Message']['required']
    assert 'execution_engine' in doc['components']['schemas']['ArtifactVersion']['required']

    invalid = []
    def bad(name, data, label):
        invalid.append((name, data, label))
    image = builder.EXAMPLES['ImageTaskCreate']
    video = builder.EXAMPLES['VideoTaskCreate']
    for field,value in [('model','paid-model'),('base_url','https://invalid.example'),
                        ('user_id',builder.U),('output_path','/tmp/anywhere'),('n',10)]:
        bad('TaskCreate',{**image,field:value},'拒绝额外参数 '+field)
    for seconds in [3,13,4.5]:
        bad('TaskCreate',{**video,'seconds':seconds},'拒绝视频秒数 '+str(seconds))
    bad('TaskCreate',{**video,'size_tier':'1080P'},'拒绝Flash高分辨率切换')
    bad('TaskCreate',{**video,'mode':'reference'},'不支持未纳入首版的reference模式')
    bad('TaskCreate',{k:v for k,v in video.items() if k!='reference_confirmation_id'},'首帧必须有确认ID')
    bad('TaskCreate',{**video,'mode':'text'},'文生视频禁止伪装带首帧确认')
    bad('TaskCreate',{**image,'target_artifact_id':builder.A},'修改作品必须带base版本')
    bad('TaskCreate',{**image,'base_version_id':builder.V},'base版本必须带目标作品')
    bad('TaskCreate',{**image,'reference_version_ids':[builder.V, builder.V]},'参考版本不重复')
    bad('MessageCreate',{'client_message_id':builder.M,'content':''},'不能空消息无附件')
    bad('MessageCreate',{**builder.EXAMPLES['MessageCreate'],'role':'assistant'},'不接受伪造消息角色')
    bad('MessageCreate',{**builder.EXAMPLES['MessageCreate'],'provider_conversation_id':builder.C},'不接受CLI ID')
    bad('MessageCreate',{**builder.EXAMPLES['MessageCreate'],'generation_permission':'unrestricted'},'无无限生成许可')
    bad('MessageCreate',{'client_message_id':builder.M,'content':'x'*16001},'请求长度安全边界')
    bad('GenerationPolicyPatch',{},'暂停设置必须有变更字段')
    bad('GenerationPolicyPatch',{'api_key':'not-a-real-key'},'管理员接口不暴露任意凭据配置')
    for name,value,label in invalid:
        try:
            validate(name,value)
        except Exception as exc:
            from jsonschema import ValidationError
            if not isinstance(exc,ValidationError):
                raise
        else:
            raise AssertionError('无效输入意外通过: '+label)
    text_video={k:v for k,v in video.items() if k!='reference_confirmation_id'}
    text_video['mode']='text'
    validate('TaskCreate',text_video)
    for name in ['ImageTaskCreate','VideoTaskCreate','LocalMotionTaskCreate']:
        validate('TaskCreate',builder.EXAMPLES[name])
    validate('MessageCreate',{'client_message_id':builder.M,'content':'','attachment_version_ids':[builder.V]})
    validate('TaskCreate',{**image,'target_artifact_id':builder.A,'base_version_id':builder.V})

    guide=(ROOT/'FastAPI接口文档-v1.md').read_text()
    guide_examples = 0
    for name,raw in re.findall(r'▶ 示例 schema：(\w+)\s*```json\s*([\s\S]*?)```',guide):
        validate(name,json.loads(raw));guide_examples+=1
    assert guide_examples>=7
    sse_frames = 0
    for raw in re.findall(r'^data: (\{.*\})$',guide,re.M):
        validate('ConversationEvent',json.loads(raw));sse_frames+=1
    assert sse_frames>=1
    index=(ROOT/'路由索引-v1.md').read_text()
    for path,operations in doc['paths'].items():
        for method,op in operations.items():
            assert f'• {method.upper()} {path} —' in index
            assert op['operationId'] in index
    if args.openapi_meta:
        meta=json.loads(args.openapi_meta.read_text())
        assert meta.get('$id','').startswith('https://spec.openapis.org/oas/3.1/schema/')
        Draft202012Validator(meta, format_checker=FormatChecker()).validate(doc)
        print('PASS：官方OpenAPI 3.1元schema完整结构校验')
    else:
        print('提示：本次未提供官方OpenAPI元schema；已执行本地schema、路由、示例与安全声明检查。')
    print(f'PASS：{len(doc["paths"])}个路径／{methods}个操作／{len(doc["components"]["schemas"])}个schema／{len(refs)}个内部引用')
    print(f'PASS：{examples}个契约示例／{guide_examples}个文档JSON示例／{sse_frames}个SSE帧／{len(invalid)}类拒绝用例')
    print('范围：仅文档与schema；未执行运行时鉴权、FastAPI集成、多用户隔离或真实生成测试。')

if __name__=='__main__':
    main()
