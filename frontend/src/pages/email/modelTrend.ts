import type { EmailStagedModel } from "../../api/console";
export type TrendMetric = "macro_f1" | "accuracy" | "precision" | "recall" | "f1" | "p50" | "p95" | "p99";
export function modelMetric(model:EmailStagedModel,metric:TrendMetric,category:string):number|null {
  const value=metric==="macro_f1"||metric==="accuracy"?model.metrics?.[metric]
    : metric==="p50"||metric==="p95"||metric==="p99"?model.end_to_end_latency_ms?.[metric]
    :model.metrics?.categories[category]?.[metric];
  return typeof value==="number"&&Number.isFinite(value)?value:null;
}
export function trendPoints(models:EmailStagedModel[],metric:TrendMetric,category:string) {
  let previous="";let segment=0;
  return models.slice(-10).map(model=>{
    const evidence=model.evaluation;
    const categories=model.compatibility?.enabled_categories;
    const comparable=!!(evidence?.protocol&&evidence.test_digest&&evidence.comparability_key&&categories?.length);
    const key=comparable?JSON.stringify([evidence!.protocol,evidence!.test_digest,evidence!.comparability_key,[...categories!].sort()]):"";
    const value=modelMetric(model,metric,category);
    const reason=!comparable?"缺少可比较评测证据":value===null?"未测量":previous&&key!==previous?"评测协议、测试集或类别集合不同":"";
    if(!key||key!==previous||value===null)segment++;
    previous=value===null?"":key;
    return {model,value,segment,reason};
  });
}
