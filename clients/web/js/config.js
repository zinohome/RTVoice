const cfg = {
  get base() { return localStorage.getItem("rtvoice_base") || "http://127.0.0.1:9000"; },
  set base(v) { localStorage.setItem("rtvoice_base", v); },
  get bearer() { return localStorage.getItem("rtvoice_bearer") || ""; },
  set bearer(v) { localStorage.setItem("rtvoice_bearer", v); },
  authHeaders() {
    return this.bearer ? { Authorization: `Bearer ${this.bearer}` } : {};
  },
};
export default cfg;
