# Palimpsest

Kendi bilgisayarınızda çalışan, yerel bir kitap çeviri aracı. Elyazmanız ve
API anahtarınız, yalnızca çeviri çağrılarının kendisi dışında makinenizden
hiç çıkmaz. Araç kitabı bir kez okuyup sizin kontrolünüzdeki bir brief ve
terim listesi çıkarır, ardından paragraf paragraf çevirir — her paragrafı
bağlam içinde taslak hazırlayıp gözden geçirip düzelterek — ve çalışmanızı
otomatik olarak kaydeder.

---

## İlk kurulum (yaklaşık beş dakika, bir kez)

1. Bilgisayarınızda yoksa **Python 3.10 veya üstünü** https://python.org
   adresinden kurun. (Windows'ta kurulum sırasında "Add Python to PATH"
   kutusunu işaretleyin.)

2. **API anahtarınızı ekleyin.** `key.txt.example` dosyasını açın, Anthropic
   anahtarınızı ilk satıra yapıştırın ve dosyanın adını **`key.txt`** olarak
   değiştirin. Anahtarı https://console.anthropic.com → API keys üzerinden
   alabilirsiniz. Anahtar bilgisayarınızda kalır.

3. Hepsi bu kadar. Başlatıcı, ihtiyaç duyduğu üç küçük kütüphaneyi ilk
   çalıştırmada sizin için kurar.

---

## Kullanmak için (her seferinde)

- **Mac:** **`start.command`** dosyasına çift tıklayın
- **Windows:** **`start.bat`** dosyasına çift tıklayın

Tarayıcınız uygulamada otomatik açılır. İşiniz bitince tarayıcı sekmesini
kapatın ve başlatıcının açtığı küçük terminal penceresini kapatın.

> Mac'te `start.command` ilk seferde açılmazsa, sağ tıklayın → Aç → Aç.
> Bunu yalnızca bir kez yapmanız gerekir.

---

## Nasıl çalışır

1. **Bir kitap başlatın.** Bir ad verin, dilleri ayarlayın, bir `.txt`, `.md`
   veya `.docx` dosyası bırakın ve *Kitabı oku & bağlam oluştur*'a tıklayın.
2. **Bağlamı gözden geçirin.** Otomatik oluşturulan brief ve terim listesini
   düzenleyin. Bu, her paragrafla birlikte gider. İsterseniz eşleştirmek için
   bir stil örneği yapıştırın.
3. **Çevirin.** Kaynak paragraflar solda, çevirileriniz sağda durur. Bir
   paragrafta *Çevir*'e tıklayarak taslak→inceleme→düzeltme döngüsünü
   çalıştırın. Sonucu düzenleyin, sonra *Onayla*. Kenar notu, incelemenin
   neyi yakaladığını gösterir.
4. **Kendi kendini kaydeder.** Her değişiklik diske yazılır. İstediğiniz
   zaman kapatın; kitaplarınız, aylar sonra bile, döndüğünüzde *Kitaplarınız*
   ekranında sizi bekliyor olur.
5. **Dışa aktarma**, tamamlanmış çevirinizi bir metin dosyasına yazar.

---

## Çalışmanız nerede tutulur

Her kitap, bu README'nin yanındaki **`projects/`** klasöründe bir JSON
dosyasıdır. Çalışmanızı yedeklemek veya taşımak için o klasörü kopyalayın.
Bir projeyi başkasına vermek için tek JSON dosyasını gönderin.

---

## Bir şey çalışmazsa

- **"No API key found"** — dosyanın adının tam olarak `key.txt` olduğundan
  (`key.txt.txt` değil) ve bu klasörde durduğundan emin olun, sonra sayfayı
  yenileyin.
- **Tarayıcı açılmadı** — http://localhost:5001 adresine elle gidin.
- **"python not found"** — Python kurulu değil ya da PATH'e eklenmemiş;
  python.org'dan yeniden kurup tekrar deneyin.

---

## Maliyeti nedir

API çağrıları için doğrudan Anthropic'e ödeme yaparsınız (hiçbir şey
aradaki başka birinden geçmez). Kitabın tek seferlik bütün-kitap okumasının
ve her paragrafın maliyeti, ilerledikçe size gösterilir. Tam boy bir roman,
seçtiğiniz modele göre tipik olarak birkaç dolardan düşük çift haneli
rakamlara kadar tutar.
