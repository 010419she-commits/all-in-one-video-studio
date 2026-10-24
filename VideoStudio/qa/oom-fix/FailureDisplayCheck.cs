using System;
using System.Collections.Generic;
namespace LocalVideoStudio {
internal static class FailureDisplayCheck {
 static void Check(bool ok,string name){if(!ok)throw new Exception(name);}
 static Dictionary<string,object> Failed(object detail){return new Dictionary<string,object>{{"connected",true},{"busy",false},{"phase","failed"},{"detail",detail}};}
 static int Main(){try{
  string reason="显存不足：本次还需 5.30 GiB，当时剩余 2.99 GiB。请缩短单段时长、降低分辨率或减少参考图。";
  var oom=GenerationProgressState.From(Failed(reason),true,false);
  Check(oom.Failed&&oom.Title.Contains("显存不足")&&oom.Detail==reason,"OOM reason must reach the visible progress message");
  var other=GenerationProgressState.From(Failed("节点 8（SamplerCustomAdvanced）执行失败。"),true,false);
  Check(other.Detail.Contains("SamplerCustomAdvanced")&&other.Title.Contains("失败"),"Other safe error summaries must remain visible");
  var empty=GenerationProgressState.From(Failed("  "),true,false);
  Check(empty.Detail=="生成已停止，请查看任务状态。","Legacy empty errors need a useful fallback");
  var longError=GenerationProgressState.From(Failed(new String('x',500)),true,false);
  Check(longError.Detail.Length==240,"Error detail must stay bounded");
  Check(!GenerationProgressState.From(Failed(reason),true,true).Failed,"A new submission must replace the previous failure");
  Console.WriteLine("PASS: failure reason visible; OOM title; legacy fallback; bounded detail; new submission resets failure.");return 0;
 }catch(Exception e){Console.WriteLine("FAIL: "+e.Message);return 1;}}
}
}
