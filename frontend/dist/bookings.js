/** Local demonstration orders only; no booking/refund API is implied. */
export const isBookingCancelled=booking=>booking?.status==='cancelled';

export function cancelDemoBooking(booking,when=new Date()){
  if(!booking||typeof booking.id!=='string')throw new TypeError('Не удалось найти поездку.');
  if(isBookingCancelled(booking))return booking;
  return {...booking,status:'cancelled',cancelledAt:new Date(when).toISOString(),cancellation:{mode:'demo',scope:'entire_trip',refundStatus:'simulated'}};
}
