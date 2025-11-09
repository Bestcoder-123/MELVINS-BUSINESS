const express = require("express");
const path = require("path");
const app = express();
const PORT = process.env.PORT || 3000;

<<<<<<< HEAD
// Serve files inside the "Shop_Manager" folder
app.use(express.static(path.join(__dirname, "Shop_Manager")));
=======
// Serve the files in this same folder (Shop_Manager)
app.use(express.static(__dirname));
>>>>>>> 24823ee (Initial commit - add Express server and project files)

app.listen(PORT, () => {
  console.log(`✅ Melvins-Shop running on port ${PORT}`);
});
