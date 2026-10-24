# Değişiklikler

## 1.1.0 — 2026-10-25

### Yeni
- **PDF içe aktarma.** Kitabı PDF olarak yükleyebilirsiniz: başlıklar, paragraflar, listeler, epigraf, alıntı,
  şiir, atıf ve kutular ayrı ayrı tanınır; görseller ve tablolar yer tutucu ya da tablo olarak gelir, dizin
  çeviriye girmez. Üst/alt bilgi, sayfa numarası, filigran ayıklanır; sayfa geçişinde bölünen paragraflar ve
  satır sonu tireleri birleştirilir.
- **Kaynak önizleme.** Her paragrafın yanından PDF'teki sayfasını, paragraf işaretli olarak açabilirsiniz.
- **Dipnot ve son notlar.** Sayfa altı dipnotları ve kitap/bölüm sonundaki "Notes" bölümleri metindeki atıflarla
  eşleştirilir; her not, ona ilk atıf yapan paragrafın hemen ardına gelir. Eşleşmeyenler işaretlenir.
- **Kitap içi sayfa göndermeleri** ("see p. 42" gibi) işaretlenir: Türkçe baskıda sayfa numarası değişeceği için
  kenarda küçük bir simge, Word çıktısında yorum olarak.
- **Taranmış PDF'ler.** Metin katmanı olmayan taranmış PDF açık bir mesajla reddedilir (ne yapılacağıyla); OCR
  katmanı bozuksa yedek bir okuyucuyla yeniden okunur, yine de çok bozuksa reddedilir. Taranmış sayfalarda şüpheli
  sözcüklerin altı noktalı çizilir; tıklayınca kaynak sayfa açılır.
- **Word dışa aktarma.** Çeviri, biçemleriyle (başlıklar, epigraf, alıntı, şiir, dipnotlar) Word dosyası olarak;
  ayrıca kaynak | çeviri iki sütunlu bir redaksiyon kopyası.

### Değişenler
- **Markdown dışa aktarma** artık `<proje adı>.md` olarak iniyor (önceden `.txt`).
- **Başlatıcılar** (`start.command`, `start.bat`) kütüphaneleri uygulama klasöründeki kendi ortamına (`.venv`)
  kurar ve bir güncelleme yeni kütüphane getirdiğinde onu kendiliğinden kurar. İlk açılış birkaç dakika sürebilir.
  İnternet yokken ilk açılış durur ve nedenini söyler; daha önce kurulmuş bir ortam varsa onunla açılır.
- Varsayılan model Claude Opus 5.5; model listesi güncellendi.

### Düzeltilenler
- Türkçe ayarlı sistemlerde arama ve sözlükte "I" harfinin bozulması.
- Kurulum özetinin kesik ya da biçimsiz gelmesi artık kabul edilmiyor.
- Model bir dipnot işaretinin önüne boşluk koyduğunda dipnot numarası sözcükten ayrık kalıyordu.

### Bilinen sınırlar
- Windows başlatıcısı (`start.bat`) gerçek bir Windows'ta denenmedi.

## 1.0.0 — 2026-08-04
- İlk yayın: kitabı bir kez okuyup özet ve terim listesi çıkaran, paragraf paragraf çeviren, çalışmayı otomatik
  kaydeden yerel çeviri aracı; Homebrew ile kurulum.
