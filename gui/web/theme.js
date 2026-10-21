// Load before styles: restore the desktop profile's choice before first paint.
(() => {
  const key='agent-room-theme';
  const system=window.matchMedia('(prefers-color-scheme: dark)');
  let preference=null;
  try{const saved=localStorage.getItem(key);if(saved==='light'||saved==='dark')preference=saved}catch{/* Storage may be disabled. */}
  function apply(theme){
    document.documentElement.dataset.theme=theme;
    const button=document.getElementById('theme-toggle');
    if(button){
      const label=theme==='dark'?'切换到浅色模式':'切换到深色模式';
      button.title=label;button.setAttribute('aria-label',label);
      button.setAttribute('aria-pressed',String(theme==='dark'));
    }
  }
  apply(preference||(system.matches?'dark':'light'));
  system.addEventListener('change',event=>{if(!preference)apply(event.matches?'dark':'light')});
  document.addEventListener('DOMContentLoaded',()=>{
    apply(document.documentElement.dataset.theme);
    document.getElementById('theme-toggle').addEventListener('click',()=>{
      preference=document.documentElement.dataset.theme==='dark'?'light':'dark';
      try{localStorage.setItem(key,preference)}catch{/* Keep this window's choice. */}
      apply(preference);
    });
  });
})();
