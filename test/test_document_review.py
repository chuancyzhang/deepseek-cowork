"""Version, evidence, budget and protocol boundaries for independent review."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image
from core import document_review as review
from core.document_preview import session_preview_root
from core.llm.providers import OpenAIProvider, AnthropicProvider

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('review_document_tools',ROOT/'skills/documents/impl.py')
documents=importlib.util.module_from_spec(spec);spec.loader.exec_module(documents)


class EchoReviewer:
    supports_vision=True
    model_name='test-vision'
    provider_name='fixture'
    base_url='https://example.invalid'
    api_protocol='chat_completions'

    def __init__(self, mode='valid'):
        self.mode=mode;self.calls=[]

    def chat_stream(self,messages,tools,prompt_cache_key,request_context):
        self.calls.append(copy.deepcopy(messages))
        assert len(messages)==2 and not tools and prompt_cache_key is None
        assert request_context['max_retries']==0
        parts=messages[1]['content_parts']
        wire={'images':['data:image/png;base64,'+base64.b64encode(Path(p['path']).read_bytes()).decode() for p in parts]}
        request_context['validate_request']('chat_completions',{} if self.mode=='missing' else wire)
        if self.mode=='partial': return
        identity=json.loads(messages[1]['content'])
        response={key:identity[key] for key in ('request_id','source_version','render_configuration','kind')}
        response['pages']=[{'page':p,'summary':'fixture observation','issues':[]} for p in identity['page_numbers']]
        if self.mode=='wrong_version': response['source_version']='wrong'
        if self.mode=='source_removed': Path(self.source).unlink()
        yield {'type':'content','content':json.dumps(response)}
        if self.mode!='no_usage': yield {'type':'usage','usage':{'total_tokens':120}}
        yield {'type':'provider_terminal','status':'completed','response_id':'response-fixture'}


class DocumentReviewTests(unittest.TestCase):
    def setUp(self):
        scratch=ROOT/'.tmp/document-review-tests';scratch.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=scratch);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.cache=patch('core.document_preview.cache_root',return_value=self.root/'cache');self.cache.start();self.addCleanup(self.cache.stop)
        self.source=self.root/'source.pdf';self.source.write_bytes(b'%PDF-test')
        self.provider=EchoReviewer()
        self.context={'session_id':'session-a','run_id':'run-a','model_supports_vision':True,'document_review_provider':lambda:self.provider}

    def batch(self,page=1,**kwargs):
        output=session_preview_root('session-a')/'evidence'/str(time.time_ns());output.mkdir(parents=True)
        Image.new('RGB',(100,160),'white').save(output/'page.png')
        rendered={'source_version':hashlib.sha256(self.source.read_bytes()).hexdigest(),'render_configuration':'configuration-a',
            'renderer':'fixture','page_count':6,'pages':[{'page':page,'file':'page.png','width':100,'height':160}]}
        rendered.update(kwargs.pop('rendered',{}))
        return review.review_rendered_batch(self.source,rendered,output,self.context,**kwargs)

    def test_valid_batch_is_independent_and_coverage_is_versioned(self):
        first=self.batch(constraints='字体 14pt，保留模板蓝色')
        second=self.batch(page=4,constraints='字体 14pt，保留模板蓝色')
        self.assertEqual(second['conclusion_pages'],[1,4]);self.assertEqual(second['budget']['requests'],2)
        self.assertNotIn('images',second);self.assertNotIn('path',json.dumps(second))
        self.assertEqual(json.loads(self.provider.calls[-1][1]['content'])['layout_constraints'],'字体 14pt，保留模板蓝色')
        changed_constraints=self.batch(page=2,constraints='字体 16pt')
        self.assertEqual(changed_constraints['conclusion_pages'],[2])
        self.source.write_bytes(b'%PDF-version2')
        new=self.batch()
        self.assertEqual(new['conclusion_pages'],[1]);self.assertNotEqual(first['source_version'],new['source_version'])
        records=review.read_reviews('session-a',self.source,fingerprint=new['source_version'],configuration='configuration-a')
        self.assertFalse(records['batches'][-1]['current_version']);self.assertTrue(records['current']['current_version'])
        self.assertEqual(review.read_reviews('session-b',self.source)['batches'],[])

    def test_completed_transport_with_wrong_ack_does_not_count_as_conclusion(self):
        self.provider.mode='wrong_version';result=self.batch()
        self.assertEqual(result['state'],'failed');self.assertEqual(result['delivered_pages'],[1])
        self.assertEqual(result['conclusion_pages'],[])

    def test_missing_image_or_terminal_never_counts_delivered(self):
        for mode in ('missing','partial'):
            self.context['run_id']=mode;self.provider.mode=mode
            result=self.batch()
            self.assertEqual(result['state'],'failed');self.assertEqual(result['delivered_pages'],[])

    def test_unchanged_repeat_reuses_evidence_without_spending(self):
        self.batch();again=self.batch()
        self.assertTrue(again['reused']);self.assertEqual(len(self.provider.calls),1)

    def test_overview_does_not_claim_detail_coverage(self):
        result=self.batch(kind='overview',rendered={'pages':[{'page':1,'page_numbers':[1,4,6],'file':'page.png','width':100,'height':160}]})
        self.assertEqual(result['conclusion_pages'],[]);self.assertEqual(result['budget']['page_times'],3)

    def test_evidence_is_historical_and_tampering_is_detected(self):
        self.provider.mode='source_removed';self.provider.source=self.source
        result=self.batch()
        self.assertEqual(result['state'],'completed');self.assertTrue(result['source_changed'])
        batch=review.read_reviews('session-a',self.source)['batches'][0]
        digest=batch['images'][0]['sha256']
        image=review.review_image('session-a',self.source,batch['id'],digest)
        with self.assertRaises(ValueError): review.review_image('session-b',self.source,batch['id'],digest)
        image.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'变化'): review.review_image('session-a',self.source,batch['id'],digest)

    def test_budgets_cover_failures_and_versions_and_unknown_usage_stops(self):
        self.provider.mode='no_usage';first=self.batch()
        self.assertEqual(first['state'],'completed')
        self.assertEqual(self.batch(page=2)['state'],'not_reviewed')
        self.provider.mode='valid';self.context['run_id']='versions'
        for index in range(4):
            self.source.write_bytes(f'%PDF-{index}'.encode());result=self.batch()
        self.assertEqual(result['state'],'not_reviewed');self.assertIn('两轮',result['reason'])
        self.context['run_id']='retries';self.provider.mode='wrong_version'
        with patch.dict(review.LIMITS,requests=2):
            self.batch();self.batch();result=self.batch()
        self.assertEqual(result['budget']['requests'],2);self.assertEqual(result['state'],'not_reviewed')

    def test_clock_begins_before_conversion_and_abandoned_review_is_interrupted(self):
        review.begin_review(self.context)
        path=session_preview_root('session-a')/'reviews'/(review._key('run-a')+'.json')
        state=review._load(path);state['started_at']-=400;review._save(path,state)
        with self.assertRaisesRegex(Exception,'6 分钟'): review.begin_review(self.context)
        self.context['run_id']='interrupt';self.batch()
        path=path.with_name(review._key('interrupt')+'.json');state=review._load(path);state['batches'][0]['state']='reviewing';review._save(path,state)
        self.assertEqual(review.read_reviews('session-a',self.source)['batches'][0]['state'],'interrupted')
        self.assertEqual(self.batch(page=2)['state'],'not_reviewed')

    def test_task_copy_preserves_source_and_reuses_edited_draft(self):
        original=self.source.read_bytes()
        result=documents.document_copy(str(self.root),str(self.source),self.context)
        self.assertTrue(result['ok'],result)
        draft=self.root/result['path'];draft.write_bytes(b'%PDF-edited')
        again=documents.document_copy(str(self.root),str(self.source),self.context)
        self.assertTrue(again['reused']);self.assertEqual(draft.read_bytes(),b'%PDF-edited')
        self.assertEqual(self.source.read_bytes(),original)
        self.context['session_id']='other'
        self.assertNotEqual(result['path'],documents.document_copy(str(self.root),str(self.source),self.context)['path'])

    def test_protocol_guard_checks_real_serialized_bytes_before_sdk(self):
        picture=self.root/'image.png';Image.new('RGB',(20,30),'white').save(picture)
        digest=hashlib.sha256(picture.read_bytes()).hexdigest()
        messages=[{'role':'user','content':'inspect','content_parts':[{'type':'input_image','path':str(picture)}]}]
        for protocol in ('chat_completions','responses','anthropic'):
            with self.subTest(protocol=protocol):
                client=MagicMock()
                if protocol=='anthropic':
                    provider=AnthropicProvider.__new__(AnthropicProvider)
                    provider.supports_vision=True;provider.model_name='fixture';provider.base_url='https://example.invalid';provider.client=client
                    stream=provider._anthropic_stream
                else:
                    module=ModuleType('openai');module.OpenAI=MagicMock(return_value=client)
                    with patch.dict(sys.modules,openai=module):
                        provider=OpenAIProvider('unused','https://example.invalid','fixture',supports_vision=True,api_protocol=protocol,thinking_enabled=False)
                    stream=provider._responses_stream if protocol=='responses' else provider._chat_completions_stream
                checked=[]
                def validate(name,params):
                    self.assertEqual(name,protocol);self.assertEqual(list(review._wire_images(params)),[digest]);checked.append(name)
                    raise ValueError('guard stops before network')
                with self.assertRaisesRegex(ValueError,'guard stops'):
                    list(stream(messages,request_context={'validate_request':validate,'tools_disabled':True}))
                self.assertEqual(checked,[protocol]);client.chat.completions.create.assert_not_called();client.responses.create.assert_not_called();client.messages.stream.assert_not_called()

    def test_permanent_cleanup_only_removes_owned_conversation_cache(self):
        from core.document_preview import delete_session_previews
        self.batch();other=session_preview_root('session-b');(other/'retained.txt').write_text('keep')
        delete_session_previews('session-a')
        self.assertEqual(review.read_reviews('session-a',self.source)['batches'],[])
        self.assertTrue((other/'retained.txt').exists());self.assertTrue(self.source.exists())

    def test_renderer_recovery_never_uses_com_or_bypasses_a_blocked_engine(self):
        from core import office_renderers as engines
        marker=self.root/'native.json';marker.write_text('{}')
        with patch.object(engines,'state_path',return_value=marker),patch.object(engines,'_mutex_busy',return_value=True),patch.object(engines,'_office_processes') as processes:
            self.assertEqual(engines.native_state('powerpoint')['state'],'busy');processes.assert_not_called()
        with patch.object(engines,'state_path',return_value=marker),patch.object(engines,'_mutex_busy',return_value=False),patch.object(engines,'_office_processes',return_value=[123]):
            self.assertEqual(engines.native_state('powerpoint')['state'],'blocked')
        with patch.object(engines,'renderer_catalog',return_value={'candidates':[{'id':'office','available':False,'reason':'cleanup pending'},{'id':'libreoffice','available':True}]}):
            with self.assertRaisesRegex(Exception,'cleanup pending'): engines.select_renderer('powerpoint')


if __name__=='__main__': unittest.main()
