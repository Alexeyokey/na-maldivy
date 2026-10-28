import test from 'node:test';
import assert from 'node:assert/strict';
import {cancelDemoBooking,isBookingCancelled} from '../dist/bookings.js';
const booking={id:'DEMO-123456',hotel:'kazan-light',total:30550,state:{city:'kazan',checkIn:'2026-11-06',checkOut:'2026-11-09',transfer:true},reminder:true};
const when=new Date('2026-10-28T12:00:00Z');

test('Existing orders without a status remain active and can be cancelled',()=>{
 assert.equal(isBookingCancelled(booking),false);const cancelled=cancelDemoBooking(booking,when);
 assert.equal(cancelled.status,'cancelled');assert.equal(cancelled.cancelledAt,when.toISOString());assert.equal(isBookingCancelled(cancelled),true);
});
test('Cancellation preserves the whole order and does not mutate its original data',()=>{
 const before=structuredClone(booking);const cancelled=cancelDemoBooking(Object.freeze({...booking,status:'confirmed'}),when);
 assert.deepEqual(booking,before);assert.equal(cancelled.id,booking.id);assert.equal(cancelled.hotel,booking.hotel);assert.equal(cancelled.total,booking.total);assert.deepEqual(cancelled.state,booking.state);
 assert.equal(cancelled.reminder,true,'A calendar event already downloaded must not be reported as removed');
});
test('Repeated cancellation keeps the original cancellation time',()=>{
 const cancelled=cancelDemoBooking(booking,when);assert.equal(cancelDemoBooking(cancelled,new Date('2026-10-29T12:00:00Z')),cancelled);
});
test('Cancellation is explicitly simulated and survives browser-style serialization',()=>{
 const cancelled=JSON.parse(JSON.stringify(cancelDemoBooking(booking,when)));assert.equal(isBookingCancelled(cancelled),true);assert.deepEqual(cancelled.cancellation,{mode:'demo',scope:'entire_trip',refundStatus:'simulated'});assert.equal(cancelled.cancelledAt,when.toISOString());
});
test('Missing orders cannot be cancelled',()=>{
 assert.throws(()=>cancelDemoBooking(null,when),TypeError);assert.throws(()=>cancelDemoBooking({},when),TypeError);
});
