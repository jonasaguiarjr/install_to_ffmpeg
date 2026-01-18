const express = require("express");
const { exec } = require("child_process");
const app = express();
const PORT = 3000;

app.use(express.json());

app.post("/analyze", (req, res) => {
  const { url } = req.body;
  if (!url) return res.status(400).send({ error: "Missing 'url' field" });

  exec(
    `ffprobe -i "${url}" -show_entries format=duration -v quiet -of csv="p=0"`,
    (error, stdout, stderr) => {
      if (error) {
        return res.status(500).json({ error: stderr });
      }
      res.json({ duration: stdout.trim() });
    }
  );
});

app.listen(PORT, () => console.log(`FFmpeg API running on port ${PORT}`));
