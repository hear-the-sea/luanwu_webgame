(function () {
  "use strict";

  document.querySelectorAll(".ring-loadout-overlay").forEach((overlay) => {
    document.body.appendChild(overlay);
  });

  document.querySelectorAll("[data-ring-lineup-form]").forEach((form) => {
    const guests = Array.from(form.querySelectorAll('input[name="guest_ids"]'));
    const troops = Array.from(form.querySelectorAll('input[name^="troop_"]'));
    const maximumGuests = Number(form.dataset.maxGuests) || 20;
    const capacityLabel = form.querySelector("[data-ring-capacity]");
    const errorLabel = form.querySelector("[data-ring-error]");
    const selectedCapacity = () => guests.reduce((sum, guest) => sum + (guest.checked ? Number(guest.dataset.capacity) : 0), 0);
    const refresh = () => {
      const total = troops.reduce((sum, troop) => sum + Math.max(0, Number(troop.value) || 0), 0);
      capacityLabel.textContent = `门客 ${guests.filter((guest) => guest.checked).length} / ${maximumGuests} · 护院 ${total} / ${selectedCapacity()}`;
    };
    form.querySelector("[data-ring-select-all]").addEventListener("click", () => {
      guests.forEach((guest, index) => { guest.checked = index < maximumGuests; });
      refresh();
    });
    form.querySelector("[data-ring-clear-guests]").addEventListener("click", () => {
      guests.forEach((guest) => { guest.checked = false; });
      refresh();
    });
    form.querySelector("[data-ring-clear-troops]").addEventListener("click", () => {
      troops.forEach((troop) => { troop.value = "0"; });
      refresh();
    });
    form.querySelectorAll("[data-ring-adjust]").forEach((button) => {
      button.addEventListener("click", () => {
        const input = button.parentElement.querySelector("input");
        const requested = (Number(input.value) || 0) + Number(button.dataset.ringAdjust);
        const otherTotal = troops.reduce((sum, troop) => sum + (troop === input ? 0 : Math.max(0, Number(troop.value) || 0)), 0);
        input.value = String(Math.min(Math.max(0, selectedCapacity() - otherTotal), Math.max(0, requested)));
        refresh();
      });
    });
    guests.forEach((guest) => guest.addEventListener("change", () => {
      errorLabel.hidden = true;
      if (guests.filter((item) => item.checked).length > maximumGuests) {
        guest.checked = false;
        errorLabel.textContent = `最多上阵 ${maximumGuests} 名门客。`;
        errorLabel.hidden = false;
      }
      refresh();
    }));
    troops.forEach((troop) => troop.addEventListener("input", refresh));
    form.addEventListener("submit", (event) => {
      errorLabel.hidden = true;
      const total = troops.reduce((sum, troop) => sum + Number(troop.value), 0);
      const error = !guests.some((guest) => guest.checked) ? "请至少选择一名门客。" : guests.filter((guest) => guest.checked).length > maximumGuests ? `最多上阵 ${maximumGuests} 名门客。` : total > selectedCapacity() ? "护院人数超过所选门客的带兵上限。" : "";
      if (error) {
        event.preventDefault();
        errorLabel.textContent = error;
        errorLabel.hidden = false;
      }
    });
    refresh();
  });
})();
