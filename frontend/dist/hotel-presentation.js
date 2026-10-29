/** Hotel facts preserve unknown values and never infer amenities from a rating. */
const clock=value=>{
 if(typeof value!=='string'||!/^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,6})?)?$/.test(value))return null;
 return value.slice(0,5);
};
const rubles=value=>Number.isInteger(value)&&value>=0?value.toLocaleString('ru-RU')+' ₽':null;

export function hotelDetailRows(hotel={}){
 const h=hotel||{},checkIn=clock(h.check_in_time),checkOut=clock(h.check_out_time),closes=clock(h.reception_closes_at);
 const reception=h.reception_24h===true?'Круглосуточно':h.reception_24h===false?(closes?'До '+closes:'Не круглосуточно; часы уточняются'):'Режим работы не уточнён';
 const earlyPrice=rubles(h.early_check_in_price_rub);
 const early=h.early_check_in_available===true?(earlyPrice?'По запросу · '+earlyPrice:'По запросу · цена не уточнена'):h.early_check_in_available===false?'Не предусмотрен':'Возможность не уточнена';
 return [
  {label:'Заезд',value:checkIn?'С '+checkIn:'Время не уточнено'},
  {label:'Выезд',value:checkOut?'До '+checkOut:'Время не уточнено'},
  {label:'Стойка регистрации',value:reception},
  {label:'С детьми',value:h.adults_only===false?'Дети принимаются':h.adults_only===true?'Только для взрослых':'Правила не уточнены'},
  {label:'С питомцами',value:h.pets_allowed===true?'Разрешено; условия уточняются':h.pets_allowed===false?'Не разрешено':'Правила не уточнены'},
  {label:'Ранний заезд',value:early},
 ];
}
