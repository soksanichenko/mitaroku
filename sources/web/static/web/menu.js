/* Dropdown menus: say whether a menu should open upward.
   Below the trigger there is the viewport edge, or the fixed bottom navigation on a
   phone; when the open menu does not fit there and fits above, it opens upward. */
window.mtMenuUp = function (trigger, menu) {
  var nav = document.querySelector('.mt-bottom-nav');
  var floor = window.innerHeight;
  if (nav && getComputedStyle(nav).display !== 'none') {
    floor = nav.getBoundingClientRect().top;
  }
  var box = trigger.getBoundingClientRect();
  var height = menu.offsetHeight + 8;
  if (box.bottom + height <= floor) {
    return false;
  }
  return box.top - height > 0 || box.top > floor - box.bottom;
};
