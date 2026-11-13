// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import DesktopRemindersCard from './DesktopRemindersCard';
import DesktopReminderTarget from './DesktopReminderTarget';
import type { ReminderState } from '@/features/desktopReminders/types';
const mocks=vi.hoisted(()=>({getEvent:vi.fn(),listApplications:vi.fn()}));
vi.mock('@/services/events',()=>({getEvent:mocks.getEvent}));
vi.mock('@/services/applications',()=>({listApplications:mocks.listApplications}));
let root:Root,container:HTMLDivElement,state:ReminderState;
beforeEach(()=>{
 (globalThis as typeof globalThis & {IS_REACT_ACT_ENVIRONMENT:boolean}).IS_REACT_ACT_ENVIRONMENT=true;
 Object.defineProperty(window,'matchMedia',{configurable:true,value:()=>({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}})});
 state={preferences:{enabled:false,scopes:[],paused:false,quietEnabled:true,quietStart:22,quietEnd:8},supported:true,error:null,status:'disabled'};
 window.offerpilotReminders={getState:vi.fn(async()=>state),configure:vi.fn(async p=>({...state,preferences:p})),test:vi.fn(async()=> 'submitted'),consumeNavigation:vi.fn(async()=>null),onState:()=>()=>{},onNavigation:()=>()=>{},onTestResult:()=>()=>{}};
 container=document.createElement('div');document.body.appendChild(container);root=createRoot(container);
});
afterEach(()=>{act(()=>root.unmount());container.remove();delete window.offerpilotReminders;vi.clearAllMocks();});
it('web exposes no controls and desktop never opts in implicitly',async()=>{
 const bridge=window.offerpilotReminders!;delete window.offerpilotReminders;
 await act(async()=>root.render(<DesktopRemindersCard/>));expect(container.textContent).toBe('');
 window.offerpilotReminders=bridge;await act(async()=>root.render(<DesktopRemindersCard/>));
 expect(bridge.configure).not.toHaveBeenCalled();expect(bridge.test).not.toHaveBeenCalled();
 expect(container.querySelector('button[role="switch"]')?.hasAttribute('disabled')).toBe(true);
 await act(async()=>container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
 expect(bridge.configure).toHaveBeenCalledWith(expect.objectContaining({enabled:false,scopes:['deadline']}));
});
it('native click reads actual event and rejects mismatched application association',async()=>{
 window.offerpilotReminders!.consumeNavigation=vi.fn().mockResolvedValueOnce({applicationId:2,eventId:4}).mockResolvedValue(null);
 mocks.listApplications.mockResolvedValue([{id:2,company_name:'Synthetic Co'}]);
 mocks.getEvent.mockResolvedValue({id:4,application_id:99,notes:'Do not display'});
 await act(async()=>root.render(<DesktopReminderTarget/>));
 expect(mocks.getEvent).toHaveBeenCalledWith(4);expect(document.body.textContent).not.toContain('Do not display');expect(document.body.textContent).toContain('事项已变化');
});
it('valid native target displays synthetic record data',async()=>{
 window.offerpilotReminders!.consumeNavigation=vi.fn().mockResolvedValueOnce({applicationId:2,eventId:4}).mockResolvedValue(null);
 mocks.listApplications.mockResolvedValue([{id:2,company_name:'Synthetic Co',position_name:'Synthetic Role',status:'interview'}]);
 mocks.getEvent.mockResolvedValue({id:4,application_id:2,status:'scheduled',scheduled_at:'2026-11-13T12:00:00',location:'Synthetic Room',notes:'Synthetic agenda'});
 await act(async()=>root.render(<DesktopReminderTarget/>));expect(document.body.textContent).toContain('Synthetic agenda');
});
