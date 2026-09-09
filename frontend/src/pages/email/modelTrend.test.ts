import {expect,it} from "vitest";
import {trendPoints} from "./modelTrend";
import type {EmailStagedModel} from "../../api/console";
const model=(id:string,key:string,value:number|null)=>({model_id:id,trained_at:"",status:"candidate",metrics:{accuracy:value,macro_f1:value,categories:{}},evaluation:{protocol:"holdout",test_digest:key,comparability_key:key},compatibility:{enabled_categories:["work"],description_version:"d1"},end_to_end_latency_ms:null,head_timing_percentiles_ms:null} satisfies EmailStagedModel);
it("breaks different protocols, datasets, missing evidence and category sets",()=>{
  const points=trendPoints([model("a","one",.8),model("b","one",.9),model("c","two",.95),model("d","two",null),model("e","two",.96)],"macro_f1","");
  expect(points[0].segment).toBe(points[1].segment);
  expect(points[2].segment).not.toBe(points[1].segment);
  expect(points[3].value).toBeNull();
  expect(points[4].segment).not.toBe(points[2].segment);
});
it("does not turn null latency into zero or connect absent comparability metadata",()=>{
  const points=trendPoints([model("a","one",null),{...model("b","one",.9),evaluation:null}],"p95","");
  expect(points.every(point=>point.value===null)).toBe(true);
  expect(points[1].reason).toBeTruthy();
});
