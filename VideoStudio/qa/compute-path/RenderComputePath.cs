using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
namespace LocalVideoStudio {
internal static class RenderComputePath {
 [STAThread] static void Main(){Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
  using(var form=new StudioForm(true)){
   var flags=BindingFlags.Instance|BindingFlags.NonPublic;
   var shots=(List<Shot>)typeof(StudioForm).GetField("shots",flags).GetValue(form);
   shots[0].Preset="h3_i2v";shots[0].Seconds=15;shots[0].Width=1280;shots[0].Height=704;
   form.StartPosition=FormStartPosition.Manual;form.ShowInTaskbar=false;form.Location=new Point(-30000,-30000);form.Show();Application.DoEvents();form.Location=new Point(-30000,-30000);form.LayoutScale(1f);form.SetTheme(true,false);form.ShowPage(0);
   foreach(string path in new[]{"hd","fast"}){
    shots[0].ComputePath=path;typeof(StudioForm).GetMethod("LoadShot",flags).Invoke(form,null);
    using(var bitmap=new Bitmap(form.Width,form.Height)){form.DrawToBitmap(bitmap,new Rectangle(Point.Empty,form.Size));bitmap.Save(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"path-"+path+".png"));}
   }
  }
  Console.WriteLine("Rendered both path choices outside the desktop; Testing mode; no API or GPU actions.");
 }
}
}
