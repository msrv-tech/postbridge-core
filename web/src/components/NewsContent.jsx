import { useI18n } from '../i18n'
import { Link } from 'react-router-dom'
import { newsDestination, newsRichParts, newsText } from '../adapters/newsPresentation'

export function NewsBody({ html, text }) {
  const parts = newsRichParts(html)
  if (!parts) return newsText(text)
  return parts.map((part, index) => part.kind === 'break' ? <br key={index} />
    : part.kind === 'link' ? <a key={index} href={part.href} target="_blank" rel="noopener noreferrer">{part.text}</a>
      : part.text)
}

export function NewsMedia({ media, imageClassName }) {
  const { t } = useI18n()
  if (!media) return null
  if (media.kind === 'image') return <img className={imageClassName} src={media.url} alt="" loading="lazy" />
  return <a className="news-attachment-link" href={media.url} target="_blank" rel="noopener noreferrer">
    {t(media.kind === 'video' ? 'news.media.openVideo' : 'news.media.openFile')}
  </a>
}

export function NewsReadMore({ item }) {
  const { t } = useI18n()
  const destination = newsDestination(item)
  if (!destination) return null
  return <p className="news-card-link">{destination.to
    ? <Link to={destination.to}>{t('news.readMore')}</Link>
    : <a href={destination.href} target="_blank" rel="noopener noreferrer">{t('news.readMore')}</a>}
  </p>
}
