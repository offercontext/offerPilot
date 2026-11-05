/** Synthetic browser-only fixtures. No backend, model, provider or mailbox is used. */
export const sampleApplication = {
  id: 101, company_name: 'Example Labs', position_name: 'Frontend Engineer', job_url: '', status: 'interview',
  source: 'manual', notes: '用户原文保持不变 / user content stays unchanged', applied_at: '2026-10-10T10:00:00Z',
  created_at: '2026-10-10T10:00:00Z', updated_at: '2026-10-10T10:00:00Z',
};
export const sampleEvent = {id:201,application_id:101,event_type:'interview',subtype:'technical',tags:[],round:1,scheduled_at:'2026-10-20T10:00:00Z',duration_minutes:60,location:'Video call',notes:'',status:'scheduled',company_name:sampleApplication.company_name,position_name:sampleApplication.position_name,created_at:'2026-10-10T10:00:00Z'};
export const sampleMail = {id:'mail-fixture-1',version:1,status:'pending',action:'create_event',reason:'',time_mode:'fixed_time',proposed_fields:{event_type:'interview',scheduled_at:'2026-10-22T10:00:00Z',duration_minutes:60,location:'Video call',notes:'',round:1,tags:[]},field_evidence:{},application_candidates:[{id:101,company_name:sampleApplication.company_name,position_name:sampleApplication.position_name}],target_event_id:null,evidence:{id:'evidence-fixture-1',subject:'Interview invitation',sender:'recruiter@example.invalid',received_at:'2026-10-15T07:00:00Z',snippet:'请在 2026-10-22 10:00 UTC 参加面试。',body_fingerprint:'synthetic',truncated:false,cleared_at:null},receipt:null};
export const retainedUserBody='用户输入：请帮我准备面试。';
export const retainedAiBody='AI 原文：我们可以先整理你的项目经历。';
const personaSpec = { display_name: 'Haru', traits: ['gentle'], speaking_style: '', verbosity: 'balanced', interests_text: '', fictional_background: '', additional_style_notes: '', enabled_activity_templates: ['paper_boat', 'cloud_catalog', 'paper_stars'] };
export const samplePersona = { profile_id: '22222222-2222-4222-8222-222222222222', revision: 0, content_hash: 'synthetic-persona', policy_version: 'synthetic-core', spec: personaSpec, presets: [
  { id: 'official', spec: personaSpec }, { id: 'gentle', spec: { ...personaSpec, verbosity: 'brief' } }, { id: 'concise', spec: { ...personaSpec, traits: ['calm'], verbosity: 'brief' } },
] };
export function createFixtureApi({ retainedConversation = false, configuredAi = false } = {}) {
  const requests=[]; const unexpected=[];
  // Readiness boolean only; no credentials or provider endpoints exist in this fixture.
  const settings={version:'localization-fixture',data_dir:'Synthetic browser fixture',chat_auto_approve_writes:false,active_provider_id:'',fallback_provider_ids:[],providers:[],base_url:'',model:'',has_api_key:configuredAi,runtime_mode:'local',auth_enabled:false,has_auth_token:false,log_level:'INFO'};
  const policies={revision:1,policies:Object.fromEntries(['confirmed_memory','confirmed_readiness','knowledge_context','older_conversation_summary'].map(k=>[k,{enabled:true,max_units:4,version:'v1'}]))};
  const mailStatus={capabilities:{real_connection:false,ai_recognition:false,synthetic_connection:true},connection:null,run:null,budget:{limit:100,used:0,remaining:100},execution_location:'本地/自托管后端',recognition_mode:'offline_rules',retention:{pending_days:90,processed_days:30},notice:'识别默认离线规则；QQ连接由本机安全配置页管理，外部AI尚未开放。后端停止时不检查邮件。',available_folders:['INBOX','Archive']};
  const capability={available:false,reason:'secure_store_unavailable',backend:null,local_only:true,credential_input_allowed:false,configured:false,deletion_pending:false};
  let imported=false;
  const answer=(data,status=200)=>({data,status});
  function handle(method,url,body) {
    const parsed=new URL(url,'http://localhost');const p=parsed.pathname;
    requests.push({method,path:p,query:parsed.search,body,at:Date.now()});
    if(method==='POST'&&p==='/api/companion/presence')return answer({session_id:'fixture-session',session_quiet:false,active:body.visible,blocked:!body.visible||Boolean(body.typing||body.work_running||body.awaiting_approval||body.mock_interview||body.quiet||body.failure),blockers:[]});
    if(method==='GET') {
      if(p==='/api/auth/status')return answer({auth_enabled:false,authenticated:true});
      if(p==='/api/settings')return answer(settings);
      if(p==='/api/companion/persona')return answer(samplePersona);
      if(p==='/api/companion')return answer({identity:'11111111-1111-4111-8111-111111111111',revision:1,persona_version:'companion-v1',settings:{enabled:false,proactivity:'off',hide_status:false},state:{interests:[],activity:null,wish:null},recent_events:[],shared_participation:[],presence:{session_id:'',session_quiet:false,active:false,blocked:true,blockers:[]}});
      if(p==='/api/logs')return answer({entries:[],total:0,limit:20,offset:0,has_more:false});
      if(p==='/api/applications')return answer([sampleApplication]);
      if(p==='/api/applications/creation-context')return answer({scope_id:'browser-fixture-scope'});
      if(p==='/api/applications/duplicates')return answer({items:[],has_more:false});
      if(p==='/api/application-events')return answer([sampleEvent]);
      if(p==='/api/calendar')return answer([]);
      if(p==='/api/applications/101/material-kit')return answer({id:301,application_id:101,jd_snapshot:'',status:'draft',content_json:'{}',created_at:'2026-10-10T10:00:00Z',updated_at:'2026-10-10T10:00:00Z'});
      if(p==='/api/dashboard')return answer({total:1,board:{interview:[sampleApplication]}});
      if(p==='/api/chat/conversations' && retainedConversation)return answer([{id:401,title:'Synthetic retained conversation',title_source:'manual',mode:'general',context_type:'workspace',context_ref:'global',context_label:'Workspace',created_at:'2026-10-10T10:00:00Z',updated_at:'2026-10-10T10:00:00Z'}]);
      if(p==='/api/chat/conversations/401')return answer([{id:501,conversation_id:401,role:'user',content:retainedUserBody,created_at:'2026-10-10T10:00:00Z'},{id:502,conversation_id:401,role:'assistant',content:retainedAiBody,created_at:'2026-10-10T10:00:01Z'}]);
      if(p==='/api/chat/conversations/401/readiness-context')return answer({schema_version:1,state:'not_applicable',conversation_id:401,application_id:0,target_event_id:null,resume_id:null,ordered_version_ids:[],selection_fingerprint:'',scope_revision:1,revision:1});
      if(p==='/api/chat/conversations/401/timeline')return answer({schema_version:1,conversation_id:401,mode:'snapshot',high_watermark:2,items:[retainedUserBody,retainedAiBody].map((content,index)=>({schema_version:1,conversation_id:401,turn_id:'synthetic-turn',item_id:`turn:synthetic-turn:message:${501+index}`,item_type:index?'assistant_message':'user_message',source_refs:[`message:${501+index}`],source_revision:'1',revision:1,display_revision:1,ordinal:index+1,change_seq:index+1,payload_digest:`synthetic-${index}`,deleted:false,payload:{schema_version:1,item_id:`message:${501+index}`,message_id:501+index,operation_id:null,kind:index?'assistant_message':'user_message',content,action:null}})),next_cursor:null,cursor:'synthetic-cursor-2'});
      if(p==='/api/chat/conversations/401/execution')return answer({execution:null});
      if(p==='/api/questions/stats')return answer({total:0,new:0,practicing:0,mastered:0,due:0,today_reviews:0,streak_days:0});
      if(p==='/api/onboarding')return answer({steps:{configure_ai:true,create_primary_resume:true,create_first_application:true,send_first_pilot_message:true},completed_count:4,is_complete:true,force_open:false,dismissed:false});
      if(p==='/api/context-policies')return answer(policies);
      if(p==='/api/proactive/settings')return answer({revision:1,settings:{enabled:false,reminders_enabled:false,drafts_enabled:false,application_ids:[],timezone:'UTC',quiet_start_hour:22,quiet_end_hour:8,max_reminders_per_day:4,max_drafts_per_day:1}});
      if(p==='/api/proactive/jobs')return answer({items:[]});
      if(p==='/api/reminders/candidates')return answer({items:[]});
      if(p==='/api/job-mail/status')return answer(mailStatus);
      if(p==='/api/job-mail/secure-setup/status')return answer(capability);
      if(p==='/api/job-mail/suggestions')return answer({items:imported?[sampleMail]:[],total:imported?1:0,pending_count:imported?1:0,has_more:false});
      if(p==='/api/job-mail/suggestions/mail-fixture-1')return answer(sampleMail);
      if(p==='/api/interviews')return answer({items:[{application_id:101,event_id:201,company_name:sampleApplication.company_name,position_name:sampleApplication.position_name,scheduled_at:sampleEvent.scheduled_at,note_id:null,note_source_status:null,has_review_proposal:false,review_summary:null,has_confirmed_knowledge:false,preparation_available:true,event_status:'scheduled',duration_minutes:60,scheduled_at_state:'present'}],next_cursor:null});
      if(p==='/api/interview-practice-cases')return answer({items:[]});
      if(p==='/api/interview-practice/recommendations')return answer([]);
      if(p==='/api/interview-practice/plans')return answer([]);
      if(p==='/api/knowledge/notes')return answer({items:[]});
      if(['/api/resumes','/api/offers','/api/notes','/api/questions','/api/questions/due','/api/confirmed-memory','/api/knowledge/sources','/api/interview-stories','/api/chat/conversations','/api/applications/101/notes'].includes(p))return answer([]);
    }
    if(method==='POST'&&p==='/api/job-mail/imports'){imported=true;return answer({items:[sampleMail],deduplicated:false,evidence_id:'evidence-fixture-1'});}
    if(method==='POST'&&p==='/api/job-mail/suggestions/mail-fixture-1/preview') {
      // Match job_mail/review.py _resolve: defaults, nonempty proposal, then explicit edits.
      const fields={subtype:'',tags:[],round:0,location:'',notes:'',remind_at:null,...Object.fromEntries(Object.entries(sampleMail.proposed_fields).filter(([,value])=>value!==null&&value!=='')),...body.edited_fields};
      return answer({preview_token:'synthetic-preview',operation_id:body.operation_id,suggestion_id:sampleMail.id,suggestion_version:1,action:'create_event',application_id:101,application_snapshot:{id:101,company_name:sampleApplication.company_name,position_name:sampleApplication.position_name,status:sampleApplication.status,updated_at:sampleApplication.updated_at},target_event_id:null,edited_fields:body.edited_fields,before:{},after:{...fields,application_id:101,status:'todo'},changes:Object.keys(fields).sort().filter(field=>fields[field]!==null).map(field=>({field,before:null,after:fields[field]})),scope_version:null,expires_at:'2030-01-01T00:00:00Z',warnings:['本次仅写入事件，不改变投递阶段，不发送邮件。']});
    }
    unexpected.push({method,path:p});return answer({error_code:'unmocked_test_request',detail:'Blocked by localization fixture'},501);
  }
  return {handle,requests,unexpected};
}
