(function(){
  'use strict';

  function addProductionFilters(){
    const filters=document.querySelector('.filters');
    if(!filters || document.getElementById('production_date_from')) return;

    const grid=filters.querySelector('.grid');
    if(!grid) return;

    const from=document.createElement('div');
    from.className='field';
    from.innerHTML='<label>تاریخ تولید از</label><input id="production_date_from" class="jalali-date" placeholder="1405/06/01" autocomplete="off" readonly>';

    const to=document.createElement('div');
    to.className='field';
    to.innerHTML='<label>تاریخ تولید تا</label><input id="production_date_to" class="jalali-date" placeholder="1405/06/31" autocomplete="off" readonly>';

    grid.appendChild(from);
    grid.appendChild(to);

    setTimeout(function(){
      if(typeof initAllJalaliPickers==='function') initAllJalaliPickers();
    },100);

    const originalFetch=window.fetch;
    window.fetch=function(input,init){
      try{
        const rawUrl=typeof input==='string' ? input : (input && input.url ? input.url : '');
        if(rawUrl && rawUrl.indexOf('/management/data?')!==-1){
          const url=new URL(rawUrl,window.location.origin);
          const productionFrom=document.getElementById('production_date_from')?.value.trim() || '';
          const productionTo=document.getElementById('production_date_to')?.value.trim() || '';
          if(productionFrom) url.searchParams.set('production_date_from',productionFrom);
          else url.searchParams.delete('production_date_from');
          if(productionTo) url.searchParams.set('production_date_to',productionTo);
          else url.searchParams.delete('production_date_to');
          if(typeof input==='string') input=url.toString();
          else input=new Request(url.toString(),input);
        }
      }catch(error){
        console.error('[MANAGEMENT PRODUCTION FILTER] fetch patch error:',error);
      }
      return originalFetch(input,init);
    };

    document.querySelectorAll('.filter-actions button').forEach(function(button){
      if(button.textContent.trim()==='پاک کردن'){
        button.addEventListener('click',function(){
          const a=document.getElementById('production_date_from');
          const b=document.getElementById('production_date_to');
          if(a) a.value='';
          if(b) b.value='';
        });
      }
    });
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',addProductionFilters);
  else addProductionFilters();
})();
