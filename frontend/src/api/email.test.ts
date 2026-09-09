import {afterEach,expect,it,vi} from "vitest";
import {createEmailCategory,getEmailClassification,getEmailModelVersion,listEmailClassifications,saveEmailConfig,saveEmailPromotionConfig,saveEmailRuntimeMode} from "./console";
afterEach(()=>vi.unstubAllGlobals());
function reply(value:unknown){return new Response(JSON.stringify(value),{status:200});}
it("preserves nullable importance, provider truth, quoted text and large string IDs",async()=>{
  const id="8423079112545370123";
  const fetch=vi.fn().mockResolvedValueOnce(reply({items:[{id,important:null,classification_source:"agent",provider_classification:{state:"unobserved",category_key:null,important:null}}],meta:{total:1,page:1,page_size:50,has_more:false,next_cursor:"",snapshot_at:""}}))
    .mockResolvedValueOnce(reply({ok:true,item:{id,message_text:"body",quoted_text:"> quoted",important:false,attachment_metadata:[{filename:"report.pdf",size_bytes:1024,content:"PRIVATE",path:"/private"}]},provider_classification:{state:"categorized",category_key:"legal",important:false},observability:[]}));
  vi.stubGlobal("fetch",fetch);
  const list=await listEmailClassifications("processed",{page:1,page_size:50});
  expect(list.items[0]).toMatchObject({id,important:null,classification_source:"agent",provider_classification:{state:"unobserved"}});
  const detail=await getEmailClassification(id);
  expect(detail.item).toMatchObject({id,message_text:"body",quoted_text:"> quoted",important:false});
  expect(detail.provider_classification?.category_key).toBe("legal");
  expect(JSON.stringify(detail.item.attachment_metadata)).not.toContain("PRIVATE");
  expect(fetch.mock.calls[1][0]).toBe("/api/console/email/classifications/"+id);
});
it("sends exact versioned JSON commands to config and training endpoints",async()=>{
  const fetch=vi.fn().mockImplementation(async()=>reply({ok:true}));vi.stubGlobal("fetch",fetch);
  const fields={core_description:"Core",include:["In"],exclude:["Out"],threshold:.9,enabled:true};
  await saveEmailConfig("custom-key",{...fields,expected_current_version:"c9"});
  await createEmailCategory({...fields,category_key:"custom-key",display_name:"Custom",provider_folder_name:"Mail/Custom"});
  await saveEmailPromotionConfig({macro_f1_min:.95,category_precision_min:.95,category_validation_samples_min:20,p95_latency_max_ms:500,expected_current_version:"p9"});
  const command={mode:"model_primary" as const,model_id:"model-v2",request_id:"request-123",expected_mode:"agent_primary" as const,expected_model_id:null};
  await saveEmailRuntimeMode(command);
  expect(fetch.mock.calls.map(call=>[call[0],call[1].method])).toEqual([
    ["/api/console/email/config/custom-key","PUT"],["/api/console/email/config","POST"],["/api/console/email/promotion-config","PUT"],["/api/console/email/runtime-mode","PUT"]]);
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({...fields,expected_current_version:"c9"});
  expect(JSON.parse(fetch.mock.calls[3][1].body)).toEqual(command);
});
it("passes cancellation to model detail and retains absent evaluation as null",async()=>{
  const controller=new AbortController(),fetch=vi.fn().mockResolvedValue(reply({ok:true,model:{model_id:"model/version",metrics:null,evaluation:null,end_to_end_latency_ms:null,head_timing_percentiles_ms:null}}));vi.stubGlobal("fetch",fetch);
  const result=await getEmailModelVersion("model/version",controller.signal);
  expect(fetch).toHaveBeenCalledWith("/api/console/email/model-versions/model%2Fversion",expect.objectContaining({signal:controller.signal}));
  expect(result.model.metrics).toBeNull();expect(result.model.end_to_end_latency_ms).toBeNull();
});
